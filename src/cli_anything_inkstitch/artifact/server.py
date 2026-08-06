"""Artifact HTTP server — the bidirectional agent loop, stdlib only.

Adapts the Lavish loop to embroidery projects: the editor (browser) queues
feedback and receives live-reload/agent-reply/presence over SSE; the agent
long-polls for feedback batches and edits the design through the CLI layer.

No new dependencies: ThreadingHTTPServer + condition-variable event hub.
Streaming responses (long-poll heartbeat, SSE) use connection-close framing.
"""

from __future__ import annotations

import json
import queue
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from cli_anything_inkstitch import __version__
from cli_anything_inkstitch.artifact.sessions import SessionStore, canonical_file, session_key

POLL_HEARTBEAT_S = 15.0
DEFAULT_IDLE_TIMEOUT_S = 30 * 60
WATCH_INTERVAL_S = 0.5


class EventHub:
    """Per-session pub-sub. Subscribers get their own queue; publish fans out."""

    def __init__(self):
        self._lock = threading.Lock()
        self._subs: dict[str, list[queue.Queue]] = {}

    def subscribe(self, key: str) -> queue.Queue:
        q: queue.Queue = queue.Queue()
        with self._lock:
            self._subs.setdefault(key, []).append(q)
        return q

    def unsubscribe(self, key: str, q: queue.Queue) -> None:
        with self._lock:
            subs = self._subs.get(key, [])
            if q in subs:
                subs.remove(q)
            if not subs:
                self._subs.pop(key, None)

    def publish(self, key: str, event: dict[str, Any]) -> None:
        with self._lock:
            subs = list(self._subs.get(key, []))
        for q in subs:
            q.put(event)


class ArtifactState:
    """Shared server state: store, events, presence bookkeeping, idle tracking."""

    def __init__(self, store: SessionStore):
        self.store = store
        self.hub = EventHub()
        self._lock = threading.Lock()
        self._active_polls: dict[str, int] = {}
        self._poll_generation: dict[str, int] = {}
        self._delivered: set[str] = set()
        self.sse_clients = 0
        self.last_activity = time.monotonic()

    def next_poll_generation(self, key: str) -> int:
        """One poll per session: each new poll gets the next generation and
        older generations are told to stand down (they'd otherwise race for
        the same feedback queue and split deliveries)."""
        with self._lock:
            gen = self._poll_generation.get(key, 0) + 1
            self._poll_generation[key] = gen
        if gen > 1:
            self.hub.publish(key, {"event": "poll-superseded", "gen": gen})
        return gen

    def touch(self) -> None:
        self.last_activity = time.monotonic()

    # -- presence (Lavish: listening / working / waiting) --------------------

    def presence(self, key: str) -> str:
        with self._lock:
            if self._active_polls.get(key):
                return "listening"
            if key in self._delivered:
                return "working"
            return "waiting"

    def _publish_presence_change(self, key: str, before: str) -> None:
        after = self.presence(key)
        if after != before:
            self.hub.publish(key, {"event": "agent-presence", "state": after})

    def poll_started(self, key: str) -> None:
        before = self.presence(key)
        with self._lock:
            self._active_polls[key] = self._active_polls.get(key, 0) + 1
            self._delivered.discard(key)
        self._publish_presence_change(key, before)

    def poll_finished(self, key: str) -> None:
        before = self.presence(key)
        with self._lock:
            n = self._active_polls.get(key, 0) - 1
            if n <= 0:
                self._active_polls.pop(key, None)
            else:
                self._active_polls[key] = n
        self._publish_presence_change(key, before)

    def feedback_delivered(self, key: str) -> None:
        before = self.presence(key)
        with self._lock:
            self._delivered.add(key)
        self._publish_presence_change(key, before)

    def delivery_cleared(self, key: str) -> None:
        before = self.presence(key)
        with self._lock:
            self._delivered.discard(key)
        self._publish_presence_change(key, before)

    @property
    def busy(self) -> bool:
        with self._lock:
            return self.sse_clients > 0 or bool(self._active_polls)


class ArtifactServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, state: ArtifactState, *, idle_timeout_s: float | None = DEFAULT_IDLE_TIMEOUT_S):
        super().__init__(addr, ArtifactHandler)
        self.state = state
        self.idle_timeout_s = idle_timeout_s
        self._watch_stop = threading.Event()
        self._watched: dict[str, tuple[str, float]] = {}  # key -> (svg_path, mtime)
        # watches are in-memory, but sessions persist across restarts —
        # without re-registering, a server restart silently stops live
        # reload for every open tab (a user stared at a stale design and
        # blamed their overnight browser; the real cause was three server
        # restarts that emptied this dict)
        self._rewatch_live_sessions()
        threading.Thread(target=self._watch_loop, daemon=True).start()
        if idle_timeout_s:
            threading.Thread(target=self._idle_loop, daemon=True).start()

    # -- design-file watching (mtime polling; publishes live reload) --------

    def _rewatch_live_sessions(self) -> None:
        for key in self.state.store.keys():
            session = self.state.store.find_by_key(key)
            if not session or session.get("status") == "ended":
                continue
            try:
                svg_path = json.loads(
                    Path(session["file"]).read_text()).get("svg_path")
            except (json.JSONDecodeError, OSError, KeyError):
                continue
            if svg_path:
                self.watch_design(key, svg_path)

    def watch_design(self, key: str, svg_path: str) -> None:
        try:
            mtime = Path(svg_path).stat().st_mtime
        except OSError:
            mtime = 0.0
        self._watched[key] = (svg_path, mtime)

    def _watch_loop(self) -> None:
        while not self._watch_stop.wait(WATCH_INTERVAL_S):
            for key, (svg_path, mtime) in list(self._watched.items()):
                try:
                    current = Path(svg_path).stat().st_mtime
                except OSError:
                    continue
                if current != mtime:
                    self._watched[key] = (svg_path, current)
                    self.state.hub.publish(key, {"event": "reload"})

    def _idle_loop(self) -> None:
        while not self._watch_stop.wait(30):
            if self.state.busy:
                continue
            if time.monotonic() - self.state.last_activity > self.idle_timeout_s:
                threading.Thread(target=self.shutdown, daemon=True).start()
                return

    def stop(self) -> None:
        self._watch_stop.set()
        self.shutdown()


class ArtifactHandler(BaseHTTPRequestHandler):
    # Connection-close framing everywhere; streaming endpoints rely on it.
    protocol_version = "HTTP/1.0"

    server: ArtifactServer  # for type checkers

    # -- plumbing ------------------------------------------------------------

    def log_message(self, fmt, *args):  # noqa: A003 - silence default stderr noise
        pass

    @property
    def state(self) -> ArtifactState:
        return self.server.state

    def _json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except json.JSONDecodeError:
            return {}

    # -- routing ---------------------------------------------------------------

    def do_GET(self):  # noqa: N802
        self.state.touch()
        parsed = urlparse(self.path)
        parts = [p for p in parsed.path.split("/") if p]
        try:
            if parsed.path == "/health":
                self._json({"ok": True, "app": "cli-anything-inkstitch artifact", "version": __version__})
            elif parsed.path == "/api/poll":
                self._handle_poll(parse_qs(parsed.query))
            elif len(parts) == 2 and parts[0] == "events":
                self._handle_sse(parts[1])
            elif len(parts) == 2 and parts[0] == "session":
                self._handle_editor_page(parts[1])
            elif len(parts) == 3 and parts[0] == "api" and parts[2] == "design":
                self._handle_design(parts[1])
            elif len(parts) == 3 and parts[0] == "api" and parts[2] == "preview":
                self._handle_preview(parts[1], parse_qs(parsed.query))
            elif len(parts) == 3 and parts[0] == "api" and parts[2] == "stitches":
                self._handle_stitches(parts[1], parse_qs(parsed.query))
            elif len(parts) == 3 and parts[0] == "api" and parts[2] == "history":
                self._handle_history(parts[1])
            elif len(parts) == 3 and parts[0] == "api" and parts[2] == "gate":
                self._handle_gate(parts[1])
            elif len(parts) == 3 and parts[0] == "api" and parts[2] == "palettes":
                self._handle_palettes(parse_qs(parsed.query))
            elif len(parts) == 3 and parts[0] == "api" and parts[2] == "export":
                self._handle_export(parts[1], parse_qs(parsed.query))
            elif len(parts) == 3 and parts[0] == "api" and parts[2] == "reference-image":
                self._handle_reference_image(parts[1])
            elif len(parts) == 3 and parts[0] == "api" and parts[2] == "checkpoints":
                self._handle_checkpoints_list(parts[1])
            elif (len(parts) == 5 and parts[0] == "api"
                    and parts[2] == "checkpoints" and parts[4] == "thumbnail"):
                self._handle_checkpoint_thumbnail(parts[1], parts[3])
            else:
                self._json({"error": "not found"}, 404)
        except BrokenPipeError:
            pass
        except Exception as e:  # noqa: BLE001 - surface as 500 JSON, keep server alive
            try:
                self._json({"error": str(e)}, 500)
            except (BrokenPipeError, OSError):
                pass

    def do_POST(self):  # noqa: N802
        self.state.touch()
        parsed = urlparse(self.path)
        parts = [p for p in parsed.path.split("/") if p]
        try:
            if parsed.path == "/shutdown":
                self._json({"status": "shutting-down"})
                threading.Thread(target=self.server.stop, daemon=True).start()
            elif parsed.path == "/api/sessions":
                self._handle_open(self._read_body())
            elif parsed.path == "/api/end":
                self._handle_agent_end(self._read_body())
            elif len(parts) == 3 and parts[0] == "api":
                key, action = parts[1], parts[2]
                if action == "feedback":
                    self._handle_feedback(key, self._read_body())
                elif action == "agent-reply":
                    self._handle_agent_reply(key, self._read_body())
                elif action == "agent-status":
                    self._handle_agent_status(key, self._read_body())
                elif action == "end":
                    self._handle_user_end(key)
                elif action == "edit":
                    self._handle_edit(key, self._read_body())
                elif action in ("undo", "redo"):
                    self._handle_history_step(key, redo=action == "redo")
                elif action == "reload":
                    self.state.hub.publish(key, {"event": "reload"})
                    self._json({"status": "sent"})
                elif action == "checkpoints":
                    self._handle_checkpoint_create(key, self._read_body())
                elif action == "reference":
                    self._handle_reference_update(key, self._read_body())
                else:
                    self._json({"error": "not found"}, 404)
            elif (len(parts) == 5 and parts[0] == "api"
                    and parts[2] == "checkpoints"):
                self._handle_checkpoint_action(parts[1], parts[3], parts[4],
                                               self._read_body())
            else:
                self._json({"error": "not found"}, 404)
        except BrokenPipeError:
            pass
        except Exception as e:  # noqa: BLE001
            try:
                self._json({"error": str(e)}, 500)
            except (BrokenPipeError, OSError):
                pass

    # -- session lifecycle -------------------------------------------------------

    def _handle_open(self, body: dict) -> None:
        project = body.get("project") or ""
        if not project:
            self._json({"error": "project is required"}, 400)
            return
        file = canonical_file(project)
        if not Path(file).exists():
            self._json({"error": f"project not found: {file}"}, 404)
            return
        key = session_key(file)
        host, port = self.server.server_address[:2]
        url = f"http://127.0.0.1:{port}/session/{key}"
        session = self.state.store.upsert_session(file, url, reopen=bool(body.get("reopen")))
        if session["status"] == "user-ended":
            self._json({"key": key, "file": file, "url": session["url"], "status": "user-ended"})
            return
        if session.get("revived_from"):
            self.state.delivery_cleared(key)
        self._start_watching(key, file)
        self._json({"key": key, "file": file, "url": url, "status": "opened"})

    def _start_watching(self, key: str, project_file: str) -> None:
        try:
            data = json.loads(Path(project_file).read_text())
            svg_path = data.get("svg_path")
        except (json.JSONDecodeError, OSError):
            svg_path = None
        if svg_path:
            self.server.watch_design(key, svg_path)

    def _handle_user_end(self, key: str) -> None:
        if not self.state.store.end_session(key, "user"):
            self._json({"error": "session not found"}, 404)
            return
        self.state.delivery_cleared(key)
        self.state.hub.publish(key, {"event": "ended", "by": "user"})
        self._json({"status": "ended"})

    def _handle_agent_end(self, body: dict) -> None:
        project = body.get("project") or ""
        key = session_key(canonical_file(project))
        if not self.state.store.end_session(key, "agent"):
            self._json({"error": "session not found"}, 404)
            return
        self.state.delivery_cleared(key)
        self.state.hub.publish(key, {"event": "ended", "by": "agent"})
        self._json({"status": "ended"})

    # -- the loop ---------------------------------------------------------------

    def _handle_feedback(self, key: str, body: dict) -> None:
        session = self.state.store.queue_feedback(key, body)
        if not session:
            self._json({"error": "session not found"}, 404)
            return
        self.state.hub.publish(key, {"event": "feedback"})
        self._json({"status": "queued", "pending": len(session["pending_feedback"])})

    def _handle_agent_reply(self, key: str, body: dict) -> None:
        text = str(body.get("text") or "")
        options = [str(o) for o in (body.get("options") or []) if str(o).strip()]
        if not self.state.store.add_agent_reply(key, text, options or None):
            self._json({"error": "session not found"}, 404)
            return
        event: dict = {"event": "agent-reply", "text": text}
        if options:
            event["options"] = options
        self.state.hub.publish(key, event)
        self._json({"status": "sent"})

    def _handle_agent_status(self, key: str, body: dict) -> None:
        """Transient progress line ("checking the engine source…") shown in
        the editor's working bubble. Not stored in chat — it narrates the
        agent's in-between work, replacing the static 'agent is working…'."""
        if not self.state.store.find_by_key(key):
            self._json({"error": "session not found"}, 404)
            return
        self.state.hub.publish(key, {"event": "agent-status",
                                     "text": str(body.get("text") or "")})
        self._json({"status": "sent"})

    def _handle_poll(self, params: dict) -> None:
        project = (params.get("project") or [""])[0]
        if not project:
            self._json({"error": "project is required"}, 400)
            return
        key = session_key(canonical_file(project))
        timeout_s = float((params.get("timeout_s") or ["300"])[0])
        immediate = self.state.store.take_feedback(key)
        if immediate["status"] != "waiting":
            if immediate["status"] == "feedback":
                self.state.feedback_delivered(key)
            self._json(immediate)
            return
        # Long-poll: stream whitespace heartbeats until feedback/ended/timeout.
        # JSON parsers skip leading whitespace, so the final payload stays valid.
        my_gen = self.state.next_poll_generation(key)
        q = self.state.hub.subscribe(key)
        self.state.poll_started(key)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        try:
            deadline = time.monotonic() + timeout_s
            last_beat = time.monotonic()
            superseded = False
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    event = q.get(timeout=min(remaining, POLL_HEARTBEAT_S))
                except queue.Empty:
                    event = None
                if event is None and time.monotonic() - last_beat >= POLL_HEARTBEAT_S:
                    self.wfile.write(b" ")
                    self.wfile.flush()
                    last_beat = time.monotonic()
                if (event and event.get("event") == "poll-superseded"
                        and event.get("gen", 0) > my_gen):
                    # a newer poll owns this session now — stand down without
                    # draining the queue so the new poll gets the feedback
                    superseded = True
                    break
                if event and event.get("event") in ("feedback", "ended"):
                    break
            if superseded:
                result: dict = {"status": "superseded",
                                "hint": "a newer poll took over this session"}
            else:
                result = self.state.store.take_feedback(key)
                if result["status"] == "feedback":
                    self.state.feedback_delivered(key)
            self.wfile.write(json.dumps(result).encode("utf-8"))
            self.wfile.flush()
        except (BrokenPipeError, OSError):
            pass
        finally:
            self.state.hub.unsubscribe(key, q)
            self.state.poll_finished(key)

    def _handle_sse(self, key: str) -> None:
        session = self.state.store.find_by_key(key)
        q = self.state.hub.subscribe(key)
        self.state.sse_clients += 1
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        def write_event(name: str, data: dict) -> None:
            self.wfile.write(f"event: {name}\ndata: {json.dumps(data)}\n\n".encode("utf-8"))
            self.wfile.flush()

        try:
            write_event("chat-sync", {"chat": (session or {}).get("chat", [])})
            write_event("agent-presence", {"state": self.state.presence(key)})
            while True:
                try:
                    event = q.get(timeout=POLL_HEARTBEAT_S)
                except queue.Empty:
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                    continue
                name = event.get("event", "message")
                payload = {k: v for k, v in event.items() if k != "event"}
                write_event(name, payload)
        except (BrokenPipeError, OSError):
            pass
        finally:
            self.state.hub.unsubscribe(key, q)
            self.state.sse_clients -= 1
            self.state.touch()

    # -- design surface (editor page, design model, previews, gate) -----------

    def _handle_editor_page(self, key: str) -> None:
        session = self.state.store.find_by_key(key)
        if not session:
            self._json({"error": "session not found"}, 404)
            return
        html = _load_editor_html(key)
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle_design(self, key: str) -> None:
        session = self.state.store.find_by_key(key)
        if not session:
            self._json({"error": "session not found"}, 404)
            return
        from cli_anything_inkstitch.artifact.design_model import read_design
        self._json(read_design(session["file"]))

    def _handle_edit(self, key: str, body: dict) -> None:
        session = self.state.store.find_by_key(key)
        if not session:
            self._json({"error": "session not found"}, 404)
            return
        from cli_anything_inkstitch.artifact.design_model import apply_edits
        result = apply_edits(session["file"], body.get("ops") or [])
        self.state.hub.publish(key, {"event": "reload"})
        self._json(result)

    def _handle_history_step(self, key: str, *, redo: bool) -> None:
        session = self.state.store.find_by_key(key)
        if not session:
            self._json({"error": "session not found"}, 404)
            return
        from cli_anything_inkstitch.artifact.design_model import apply_history_step
        result = apply_history_step(session["file"], redo=redo)
        if result.get("applied"):
            self.state.hub.publish(key, {"event": "reload"})
        self._json(result)

    def _handle_preview(self, key: str, params: dict) -> None:
        session = self.state.store.find_by_key(key)
        if not session:
            self._json({"error": "session not found"}, 404)
            return
        from cli_anything_inkstitch.artifact.design_model import stitch_plan_svg
        svg = stitch_plan_svg(session["file"])
        self.send_response(200)
        self.send_header("Content-Type", "image/svg+xml")
        self.send_header("Content-Length", str(len(svg)))
        self.end_headers()
        self.wfile.write(svg)

    def _handle_palettes(self, query: dict) -> None:
        """Manufacturer thread palettes mined from the engine's .gpl files
        (embroidery/palettes.py mirrors lib/threads/palette.py parsing)."""
        from cli_anything_inkstitch.embroidery.palettes import (
            list_palettes,
            read_palette,
        )
        name = (query.get("name") or [None])[0]
        if name:
            pal = read_palette(name)
            if pal is None:
                self._json({"error": f"unknown palette: {name}"}, 404)
            else:
                self._json(pal)
        else:
            self._json({"palettes": list_palettes()})

    def _handle_history(self, key: str) -> None:
        session = self.state.store.find_by_key(key)
        if not session:
            self._json({"error": "session not found"}, 404)
            return
        from cli_anything_inkstitch.artifact.design_model import history_entries
        self._json(history_entries(session["file"]))

    def _handle_export(self, key: str, query: dict) -> None:
        """Stream the machine file as a browser download (editor Export
        button).  Same engine invocation as `export file`: the binary's
        `output` extension (engine reader lib/output.py / lib/extensions);
        format defaults to the project's machine target."""
        session = self.state.store.find_by_key(key)
        if not session:
            self._json({"error": "session not found"}, 404)
            return
        import json as _json
        from pathlib import Path as _Path
        from cli_anything_inkstitch.binary import require, run_extension
        data = _json.loads(_Path(session["file"]).read_text())
        fmt = (query.get("format") or [None])[0] \
            or (data.get("session") or {}).get("machine_target") or "dst"
        binary = require(None, data.get("session") or {})
        payload = run_extension(binary, "output", data["svg_path"],
                                args={"format": fmt}, ids=[],
                                capture_stdout=True)
        if not payload:
            self._json({"error": "engine produced no output"}, 500)
            return
        name = _Path(data["svg_path"]).stem + "." + fmt
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Disposition", f'attachment; filename="{name}"')
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    # -- reference overlay (tracing aid; view-state, never in the SVG) -----------

    def _handle_reference_image(self, key: str) -> None:
        file = self._session_file(key)
        if file is None:
            return
        import json as _json
        from pathlib import Path as _Path
        ref = (_json.loads(_Path(file).read_text()).get("session") or {}) \
            .get("reference") or {}
        path = ref.get("path")
        if not path or not _Path(path).exists():
            self._json({"error": "no reference image"}, 404)
            return
        data = _Path(path).read_bytes()
        ext = _Path(path).suffix.lower()
        ctype = {".png": "image/png", ".jpg": "image/jpeg",
                 ".jpeg": "image/jpeg", ".svg": "image/svg+xml",
                 ".webp": "image/webp", ".gif": "image/gif"}.get(
            ext, "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _handle_reference_update(self, key: str, body: dict) -> None:
        """Editor adjusts opacity/visibility/position of the overlay."""
        file = self._session_file(key)
        if file is None:
            return
        from cli_anything_inkstitch.project import ProjectFile, project_lock
        with project_lock(file):
            proj = ProjectFile.load(file)
            ref = dict(proj.session.get("reference") or {})
            if not ref.get("path"):
                self._json({"error": "no reference image set"}, 404)
                return
            for k in ("opacity", "x", "y", "scale"):
                if k in body:
                    ref[k] = float(body[k])
            if "visible" in body:
                ref["visible"] = bool(body["visible"])
            ref["opacity"] = max(0.0, min(1.0, ref.get("opacity", 0.4)))
            proj.session["reference"] = ref
            proj.save()
        self._json({"reference": ref})

    # -- checkpoints (durable flagged states; checkpoints.py) --------------------

    def _session_file(self, key: str) -> str | None:
        session = self.state.store.find_by_key(key)
        if not session:
            self._json({"error": "session not found"}, 404)
            return None
        return session["file"]

    def _handle_checkpoints_list(self, key: str) -> None:
        file = self._session_file(key)
        if file is None:
            return
        from cli_anything_inkstitch.checkpoints import list_checkpoints
        self._json({"checkpoints": list_checkpoints(file)})

    def _handle_checkpoint_create(self, key: str, body: dict) -> None:
        file = self._session_file(key)
        if file is None:
            return
        from cli_anything_inkstitch.checkpoints import create_checkpoint
        record = create_checkpoint(
            file,
            str(body.get("annotation") or ""),
            body.get("history_entry_id") or None)
        self._json({"checkpoint": record})

    def _handle_checkpoint_action(self, key: str, checkpoint_id: str,
                                  action: str, body: dict) -> None:
        file = self._session_file(key)
        if file is None:
            return
        from cli_anything_inkstitch import checkpoints as cp
        if action == "restore":
            result = cp.restore_checkpoint(file, checkpoint_id)
            self.state.hub.publish(key, {"event": "reload"})
            self._json(result)
        elif action == "annotate":
            self._json({"checkpoint": cp.annotate_checkpoint(
                file, checkpoint_id, str(body.get("annotation") or ""))})
        elif action == "delete":
            self._json(cp.delete_checkpoint(file, checkpoint_id))
        else:
            self._json({"error": "not found"}, 404)

    def _handle_checkpoint_thumbnail(self, key: str, checkpoint_id: str) -> None:
        file = self._session_file(key)
        if file is None:
            return
        from cli_anything_inkstitch.checkpoints import (
            _snapshot_path,
            _thumb_path,
            find_checkpoint,
            render_thumbnail,
        )
        from cli_anything_inkstitch.project import ProjectFile
        record = find_checkpoint(ProjectFile.load(file), checkpoint_id)
        thumb = _thumb_path(file, record["svg_sha256"])
        if not thumb.exists():
            snap = _snapshot_path(file, record["svg_sha256"])
            if snap.exists():
                render_thumbnail(snap.read_bytes(), thumb)
        if not thumb.exists():
            self._json({"error": "no thumbnail"}, 404)
            return
        data = thumb.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _handle_stitches(self, key: str, query: dict | None = None) -> None:
        session = self.state.store.find_by_key(key)
        if not session:
            self._json({"error": "session not found"}, 404)
            return
        from cli_anything_inkstitch.artifact.design_model import stitch_sequence
        # ?exclude=id1,id2 — plan WITHOUT those elements (Layers eye toggles:
        # the engine's plan SVG groups by color block, not source element, so
        # hiding honestly means re-planning the visible subset)
        exclude = set()
        for raw in (query or {}).get("exclude", []):
            exclude.update(x for x in raw.split(",") if x)
        self._json(stitch_sequence(session["file"],
                                   exclude=sorted(exclude) or None))

    def _handle_gate(self, key: str) -> None:
        session = self.state.store.find_by_key(key)
        if not session:
            self._json({"error": "session not found"}, 404)
            return
        from cli_anything_inkstitch.artifact.gate import run_gate
        self._json(run_gate(session["file"]))


def _load_editor_html(key: str) -> str:
    editor = Path(__file__).parent / "editor" / "editor.html"
    if editor.exists():
        return editor.read_text(encoding="utf-8").replace("__SESSION_KEY__", key)
    return (
        "<!doctype html><meta charset='utf-8'><title>Digitizing Artifact</title>"
        f"<p>Editor UI not built yet. Session <code>{key}</code> is live; "
        "the feedback API is available under <code>/api/</code>.</p>"
    )


def serve(state_dir: str, *, port: int = 0, idle_timeout_s: float | None = DEFAULT_IDLE_TIMEOUT_S) -> ArtifactServer:
    """Create (but don't run) the artifact server; caller drives serve_forever().

    Writes server.json (port + pid) into `state_dir` so CLI invocations can
    find a running server.
    """
    state_path = Path(state_dir)
    state_path.mkdir(parents=True, exist_ok=True)
    store = SessionStore(str(state_path / "state.json"))
    if port == 0:
        # STABLE URLS: reuse the last port this state dir served on, so
        # editor tabs survive server restarts. Fall back to ephemeral only
        # if that port is taken by someone else.
        try:
            last = json.loads((state_path / "server.json").read_text()).get("port")
        except (OSError, ValueError):
            last = None
        if last:
            try:
                server = ArtifactServer(("127.0.0.1", int(last)),
                                        ArtifactState(store),
                                        idle_timeout_s=idle_timeout_s)
                _write_server_json(state_path, server)
                _warm_capability_probe(server)
                return server
            except OSError:
                pass                      # someone else owns it — go ephemeral
    server = ArtifactServer(("127.0.0.1", port), ArtifactState(store), idle_timeout_s=idle_timeout_s)
    _write_server_json(state_path, server)
    _warm_capability_probe(server)
    return server


def _write_server_json(state_path: Path, server: ArtifactServer) -> None:
    import os
    (state_path / "server.json").write_text(
        json.dumps({"port": server.server_address[1], "pid": os.getpid()})
    )


def _warm_capability_probe(server: ArtifactServer) -> None:
    """Measure which fill methods the installed binary acts on (~30s of
    renders, cached per binary version) so the editor can flag no-effect
    options. Runs once in the background; live sessions get a reload when
    fresh verdicts land so the warnings appear without user action."""
    def work() -> None:
        try:
            from cli_anything_inkstitch.schema.probe import compute_and_cache, get_cached
            if get_cached() is not None:
                return
            compute_and_cache()
            for key in server.state.store.keys():
                server.state.hub.publish(key, {"event": "reload"})
        except Exception:  # noqa: BLE001 — a probe failure must never hurt the server
            pass
    threading.Thread(target=work, daemon=True, name="capability-probe").start()
