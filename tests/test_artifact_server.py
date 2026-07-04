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


class TestSSE:
    def _read_sse_events(self, server, key, n_events, timeout=10):
        """Collect the first n SSE events (name, data) from /events/<key>."""
        events = []
        done = threading.Event()

        def reader():
            req = urllib.request.Request(_url(server, f"/events/{key}"))
            with urllib.request.urlopen(req, timeout=timeout) as r:
                name = None
                while len(events) < n_events:
                    line = r.readline().decode("utf-8").rstrip("\n")
                    if line.startswith("event: "):
                        name = line[len("event: "):]
                    elif line.startswith("data: ") and name:
                        events.append((name, json.loads(line[len("data: "):])))
                        name = None
            done.set()

        threading.Thread(target=reader, daemon=True).start()
        return events, done

    def test_sse_initial_sync_and_agent_reply(self, server, project):
        opened = _open_session(server, project)
        events, done = self._read_sse_events(server, opened["key"], 3)
        time.sleep(0.3)  # let the reader connect and take the initial pair
        _post(server, f"/api/{opened['key']}/agent-reply", {"text": "hello"})
        assert done.wait(10)
        names = [name for name, _ in events]
        assert names[0] == "chat-sync"
        assert names[1] == "agent-presence"
        assert ("agent-reply", {"text": "hello"}) in events

    def test_sse_presence_flips_to_listening_on_poll(self, server, project):
        opened = _open_session(server, project)
        events, done = self._read_sse_events(server, opened["key"], 3)
        time.sleep(0.3)

        def poll():
            _get(server, f"/api/poll?project={project}&timeout_s=1.5")

        threading.Thread(target=poll, daemon=True).start()
        assert done.wait(10)
        assert ("agent-presence", {"state": "listening"}) in events
