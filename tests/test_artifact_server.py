"""Tests for the artifact HTTP server: session lifecycle, long-poll loop, SSE."""

from __future__ import annotations

import json
import threading
import time
import urllib.request

import pytest

from cli_anything_inkstitch.artifact.server import serve


@pytest.fixture
def project(tmp_path):
    p = tmp_path / "design.inkstitch-cli.json"
    p.write_text(json.dumps({"schema_version": 1, "svg_path": None}))
    return str(p)


@pytest.fixture
def server(tmp_path):
    srv = serve(str(tmp_path / "artifact-state"), port=0, idle_timeout_s=None)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv
    srv.stop()
    thread.join(timeout=5)


def _url(server, path):
    return f"http://127.0.0.1:{server.server_address[1]}{path}"


def _get(server, path, timeout=10):
    with urllib.request.urlopen(_url(server, path), timeout=timeout) as r:
        return r.status, r.read().decode("utf-8")


def _post(server, path, payload=None, timeout=10):
    req = urllib.request.Request(
        _url(server, path),
        data=json.dumps(payload or {}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def _open_session(server, project, **extra):
    status, body = _post(server, "/api/sessions", {"project": project, **extra})
    assert status == 200
    return body


class TestLifecycle:
    def test_health(self, server):
        status, body = _get(server, "/health")
        assert status == 200
        assert json.loads(body)["ok"] is True

    def test_open_session(self, server, project):
        body = _open_session(server, project)
        assert body["status"] == "opened"
        assert body["url"].endswith(f"/session/{body['key']}")

    def test_open_missing_project_404(self, server, tmp_path):
        status, body = _post(server, "/api/sessions", {"project": str(tmp_path / "nope.json")})
        assert status == 404

    def test_open_without_project_400(self, server):
        status, body = _post(server, "/api/sessions", {})
        assert status == 400

    def test_user_end_blocks_plain_reopen(self, server, project):
        opened = _open_session(server, project)
        _post(server, f"/api/{opened['key']}/end")
        blocked = _open_session(server, project)
        assert blocked["status"] == "user-ended"
        reopened = _open_session(server, project, reopen=True)
        assert reopened["status"] == "opened"

    def test_agent_end_allows_plain_reopen(self, server, project):
        _open_session(server, project)
        status, body = _post(server, "/api/end", {"project": project})
        assert body["status"] == "ended"
        reopened = _open_session(server, project)
        assert reopened["status"] == "opened"

    def test_editor_page_served(self, server, project):
        opened = _open_session(server, project)
        status, body = _get(server, f"/session/{opened['key']}")
        assert status == 200
        assert opened["key"] in body          # session key substituted
        assert "__SESSION_KEY__" not in body  # placeholder gone
        assert "EventSource" in body          # SSE wiring present


class TestPollLoop:
    def test_immediate_drain(self, server, project):
        opened = _open_session(server, project)
        _post(server, f"/api/{opened['key']}/feedback",
              {"objects": ["elem_11"], "text": "narrower at the top"})
        status, body = _get(server, f"/api/poll?project={project}&timeout_s=5")
        result = json.loads(body)
        assert result["status"] == "feedback"
        assert result["items"][0]["objects"] == ["elem_11"]

    def test_long_poll_wakes_on_feedback(self, server, project):
        opened = _open_session(server, project)

        def send_later():
            time.sleep(0.3)
            _post(server, f"/api/{opened['key']}/feedback", {"text": "wake up"})

        threading.Thread(target=send_later, daemon=True).start()
        t0 = time.monotonic()
        status, body = _get(server, f"/api/poll?project={project}&timeout_s=30")
        elapsed = time.monotonic() - t0
        result = json.loads(body)
        assert result["status"] == "feedback"
        assert result["items"][0]["text"] == "wake up"
        assert elapsed < 5  # woke on the event, not the timeout

    def test_poll_timeout_returns_waiting(self, server, project):
        _open_session(server, project)
        status, body = _get(server, f"/api/poll?project={project}&timeout_s=0.2")
        assert json.loads(body)["status"] == "waiting"

    def test_poll_wakes_on_user_end(self, server, project):
        opened = _open_session(server, project)

        def end_later():
            time.sleep(0.3)
            _post(server, f"/api/{opened['key']}/end")

        threading.Thread(target=end_later, daemon=True).start()
        status, body = _get(server, f"/api/poll?project={project}&timeout_s=30")
        result = json.loads(body)
        assert result["status"] == "ended"
        assert result["ended_by"] == "user"

    def test_send_and_end_drains_final_feedback(self, server, project):
        opened = _open_session(server, project)
        _post(server, f"/api/{opened['key']}/feedback", {"text": "final note"})
        _post(server, f"/api/{opened['key']}/end")
        status, body = _get(server, f"/api/poll?project={project}&timeout_s=5")
        result = json.loads(body)
        assert result["status"] == "feedback"
        assert result["ended"] is True

    def test_agent_reply_lands_in_chat(self, server, project):
        opened = _open_session(server, project)
        status, body = _post(server, f"/api/{opened['key']}/agent-reply", {"text": "snapped the rail"})
        assert body["status"] == "sent"


class TestGateEndpoint:
    SVG = """<svg xmlns="http://www.w3.org/2000/svg"
     xmlns:inkstitch="http://inkstitch.org/namespace"
     width="60mm" height="40mm" viewBox="0 0 60 40">
  <metadata><inkstitch_svg_version>3</inkstitch_svg_version></metadata>
  <path id="s" fill="none" stroke="#000" inkstitch:satin_column="True" d="{d}"/>
</svg>"""
    GOOD_D = ("M10,12 C20,8 40,8 50,12 M10,18 C20,22 40,22 50,18 "
              "M10,11 L10,19 M30,8 L30,22 M50,11 L50,19")
    DESYNCED_D = ("M10,6.9 C20,0.9 40,6 50,12 M10,20 C20,26 40,26 50,20 "
                  "M10,12 L10,20 M30,7.5 L30,24.5 M50,12 L50,20")

    def _design_project(self, tmp_path, d):
        from cli_anything_inkstitch.project import ProjectFile
        from cli_anything_inkstitch.svg.document import sha256_of
        svg = tmp_path / "design.svg"
        svg.write_text(self.SVG.format(d=d))
        proj_path = tmp_path / "design.inkstitch-cli.json"
        proj, _ = ProjectFile.load_or_create(str(proj_path))
        proj.svg_path = str(svg)
        proj.svg_sha256 = sha256_of(svg)
        proj.save()
        return str(proj_path)

    def test_gate_endpoint_ok(self, server, tmp_path):
        project = self._design_project(tmp_path, self.GOOD_D)
        opened = _open_session(server, project)
        status, body = _get(server, f"/api/{opened['key']}/gate")
        assert status == 200
        assert json.loads(body)["ok"] is True

    def test_gate_endpoint_reports_errors(self, server, tmp_path):
        project = self._design_project(tmp_path, self.DESYNCED_D)
        opened = _open_session(server, project)
        status, body = _get(server, f"/api/{opened['key']}/gate")
        result = json.loads(body)
        assert result["ok"] is False
        assert any(f["check"] == "rung_pairing" for f in result["errors"])

    def test_editor_page_has_gate_handback(self, server, project):
        opened = _open_session(server, project)
        status, body = _get(server, f"/session/{opened['key']}")
        assert "gateBanner" in body
        assert "Stitch anyway" in body

    def test_undo_redo_endpoints(self, server, tmp_path):
        project = self._design_project(tmp_path, self.GOOD_D)
        opened = _open_session(server, project)
        new_d = "M10,10 L50,10 M10,16 L50,16 M10,10 L10,16 M50,10 L50,16"
        _post(server, f"/api/{opened['key']}/edit",
              {"ops": [{"op": "set_path", "id": "s", "d": new_d}]})
        assert new_d in (tmp_path / "design.svg").read_text()
        status, body = _post(server, f"/api/{opened['key']}/undo")
        assert body["applied"]
        # d reverted (file formatting may differ after lxml round-trip)
        reverted = (tmp_path / "design.svg").read_text()
        assert "M10,12 C20,8 40,8 50,12" in reverted
        assert new_d not in reverted
        status, body = _post(server, f"/api/{opened['key']}/redo")
        assert body["applied"]
        assert new_d in (tmp_path / "design.svg").read_text()
        # nothing further to redo
        status, body = _post(server, f"/api/{opened['key']}/redo")
        assert body["applied"] is None


def _read_sse_until(server, key, want, timeout=15):
    """Collect SSE events until `want` matches one (or timeout).

    `want` is an event name or a predicate over (name, data). Never count
    events by position: the initial burst can include extras (a spurious
    reload on slow Windows runners displaced fixed-count readers and made
    these tests flaky)."""
    match = want if callable(want) else (lambda e: e[0] == want)
    events = []
    done = threading.Event()

    def reader():
        req = urllib.request.Request(_url(server, f"/events/{key}"))
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                name = None
                while not done.is_set():
                    line = r.readline().decode("utf-8").rstrip("\n")
                    if line.startswith("event: "):
                        name = line[len("event: "):]
                    elif line.startswith("data: ") and name:
                        events.append((name, json.loads(line[len("data: "):])))
                        if match(events[-1]):
                            done.set()
                        name = None
        except OSError:
            pass

    threading.Thread(target=reader, daemon=True).start()
    return events, done


class TestSSE:
    def test_sse_initial_sync_and_agent_reply(self, server, project):
        opened = _open_session(server, project)
        events, done = _read_sse_until(server, opened["key"], "agent-reply")
        time.sleep(0.3)  # let the reader connect and take the initial pair
        _post(server, f"/api/{opened['key']}/agent-reply", {"text": "hello"})
        assert done.wait(15)
        names = [name for name, _ in events]
        assert names[0] == "chat-sync"
        assert "agent-presence" in names
        assert ("agent-reply", {"text": "hello"}) in events

    def test_sse_presence_flips_to_listening_on_poll(self, server, project):
        opened = _open_session(server, project)
        events, done = _read_sse_until(
            server, opened["key"],
            lambda e: e == ("agent-presence", {"state": "listening"}))
        time.sleep(0.3)

        def poll():
            _get(server, f"/api/poll?project={project}&timeout_s=1.5")

        threading.Thread(target=poll, daemon=True).start()
        assert done.wait(15)
        assert ("agent-presence", {"state": "listening"}) in events


class TestPollSupersede:
    def test_new_poll_supersedes_old(self, server, project):
        """One poll per session: a second poll takes over; the first stands
        down WITHOUT draining the queue, so feedback goes to the newest."""
        opened = _open_session(server, project)
        results = {}

        def poll_a():
            _, body = _get(server, f"/api/poll?project={project}&timeout_s=20")
            results["a"] = json.loads(body)

        ta = threading.Thread(target=poll_a, daemon=True)
        ta.start()
        time.sleep(0.4)                      # let poll A register

        def poll_b():
            _, body = _get(server, f"/api/poll?project={project}&timeout_s=20")
            results["b"] = json.loads(body)

        tb = threading.Thread(target=poll_b, daemon=True)
        tb.start()
        time.sleep(0.4)                      # A should be superseded by now
        _post(server, f"/api/{opened['key']}/feedback", {"text": "for the new poll"})
        ta.join(timeout=10)
        tb.join(timeout=10)
        assert results["a"]["status"] == "superseded"
        assert results["b"]["status"] == "feedback"
        assert results["b"]["items"][0]["text"] == "for the new poll"


class TestAgentStatus:
    def test_status_reaches_sse(self, server, project):
        opened = _open_session(server, project)
        events, done = _read_sse_until(server, opened["key"], "agent-status")
        time.sleep(0.3)
        status, body = _post(server, f"/api/{opened['key']}/agent-status",
                             {"text": "recomputing the stitch plan…"})
        assert body["status"] == "sent"
        assert done.wait(15)
        assert ("agent-status", {"text": "recomputing the stitch plan…"}) in events

    def test_status_unknown_session_404(self, server):
        status, body = _post(server, "/api/nope/agent-status", {"text": "x"})
        assert status == 404


class TestPollTransportRobustness:
    """A poll's stdout is read programmatically (SKILL.md 'Polling
    discipline') — transport failures must come out as parseable JSON, not
    tracebacks. The real incident: `artifact stop` during a long-poll made
    the server return an empty body and the CLI died on JSONDecodeError."""

    def test_request_turns_empty_body_into_usererror(self):
        from http.server import BaseHTTPRequestHandler, HTTPServer
        from cli_anything_inkstitch.commands.artifact import _request
        from cli_anything_inkstitch.errors import UserError

        class EmptyHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *a):
                pass

        srv = HTTPServer(("127.0.0.1", 0), EmptyHandler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            with pytest.raises(UserError, match="connection lost mid-request"):
                _request(f"http://127.0.0.1:{srv.server_address[1]}/api/poll")
        finally:
            srv.shutdown()

    def test_poll_cmd_emits_server_lost_json(self, tmp_path, monkeypatch):
        from click.testing import CliRunner
        from cli_anything_inkstitch.cli import root
        from cli_anything_inkstitch.commands import artifact as artifact_cmds
        from cli_anything_inkstitch.errors import UserError
        from cli_anything_inkstitch.project import ProjectFile

        proj_path = tmp_path / "p.inkstitch-cli.json"
        proj, _ = ProjectFile.load_or_create(str(proj_path))
        proj.save()

        monkeypatch.setattr(artifact_cmds, "_ensure_server",
                            lambda state_dir: "http://127.0.0.1:1")

        def dying_request(url, **kw):
            raise UserError("artifact server connection lost mid-request: boom")

        monkeypatch.setattr(artifact_cmds, "_request", dying_request)
        result = CliRunner().invoke(root, [
            "--json", "artifact", "poll",
            "--project", str(proj_path), "--timeout-s", "1"])
        assert result.exit_code == 0, result.output
        out = json.loads(result.output)
        assert out["status"] == "server-lost"
        assert "redelivered" in out["hint"]


class TestPalettesEndpoint:
    def test_list_read_and_unknown(self, server, monkeypatch):
        from cli_anything_inkstitch.embroidery import palettes as P
        canned = {"name": "Testco",
                  "threads": [{"hex": "#112233", "name": "Navy", "number": "12"}]}
        monkeypatch.setattr(P, "list_palettes", lambda: ["Testco"])
        monkeypatch.setattr(P, "read_palette",
                            lambda n: canned if n == "Testco" else None)
        status, body = _get(server, "/api/x/palettes")
        assert status == 200
        assert json.loads(body) == {"palettes": ["Testco"]}
        status, body = _get(server, "/api/x/palettes?name=Testco")
        assert json.loads(body)["threads"][0]["hex"] == "#112233"
        import urllib.error
        with pytest.raises(urllib.error.HTTPError) as ei:
            _get(server, "/api/x/palettes?name=Nope")
        assert ei.value.code == 404


class TestCheckpointEndpoints:
    SVG = ('<svg xmlns="http://www.w3.org/2000/svg" '
           'xmlns:inkstitch="http://inkstitch.org/namespace" '
           'width="30mm" height="30mm" viewBox="0 0 113.386 113.386">'
           '<metadata><inkstitch_svg_version>3</inkstitch_svg_version></metadata>'
           '<path id="r1" d="M10,20 L100,20" fill="none" stroke="#000"/></svg>')

    def _svg_project(self, tmp_path):
        from cli_anything_inkstitch.project import ProjectFile
        from cli_anything_inkstitch.svg.document import sha256_of
        svg = tmp_path / "cpd.svg"
        svg.write_text(self.SVG)
        p = tmp_path / "cpd.inkstitch-cli.json"
        proj, _ = ProjectFile.load_or_create(str(p))
        proj.svg_path = str(svg)
        proj.svg_sha256 = sha256_of(svg)
        proj.save()
        return str(p), svg

    def test_full_roundtrip(self, server, tmp_path):
        proj_path, svg = self._svg_project(tmp_path)
        key = _open_session(server, proj_path)["key"]

        status, body = _post(server, f"/api/{key}/checkpoints",
                             {"annotation": "liked this"})
        assert status == 200
        cp = body["checkpoint"]
        assert cp["annotation"] == "liked this"

        # mutate the design, then restore via the endpoint
        from cli_anything_inkstitch.artifact.design_model import apply_edits
        apply_edits(proj_path, [{"op": "set_attr", "id": "r1",
                                 "name": "bean_stitch_repeats", "value": "4"}])
        assert "bean_stitch_repeats" in svg.read_text()
        status, body = _post(server, f"/api/{key}/checkpoints/{cp['id']}/restore")
        assert status == 200 and body["restored"] == cp["id"]
        assert "bean_stitch_repeats" not in svg.read_text()

        status, body = _post(server, f"/api/{key}/checkpoints/{cp['id']}/annotate",
                             {"annotation": "the keeper"})
        assert body["checkpoint"]["annotation"] == "the keeper"

        status, raw = _get(server, f"/api/{key}/checkpoints")
        listed = json.loads(raw)["checkpoints"]
        assert [c["annotation"] for c in listed] == ["the keeper"]

        # thumbnail is served as a PNG
        import urllib.request
        with urllib.request.urlopen(
                _url(server, f"/api/{key}/checkpoints/{cp['id']}/thumbnail"),
                timeout=10) as r:
            assert r.headers["Content-Type"] == "image/png"
            assert r.read()[:8] == b"\x89PNG\r\n\x1a\n"

        status, body = _post(server, f"/api/{key}/checkpoints/{cp['id']}/delete")
        assert body == {"deleted": cp["id"]}
        status, raw = _get(server, f"/api/{key}/checkpoints")
        assert json.loads(raw)["checkpoints"] == []
