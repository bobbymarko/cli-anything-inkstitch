"""Tests for the `artifact` CLI command group (against an in-thread server)."""

from __future__ import annotations

import json
import threading
import urllib.request

import pytest
from click.testing import CliRunner

from cli_anything_inkstitch.artifact.server import serve
from cli_anything_inkstitch.cli import root


@pytest.fixture
def project(tmp_path):
    p = tmp_path / "design.inkstitch-cli.json"
    p.write_text(json.dumps({"schema_version": 1, "svg_path": None}))
    return str(p)


@pytest.fixture
def server(tmp_path, monkeypatch):
    """In-thread server whose state dir the CLI discovers via the env override."""
    state_dir = tmp_path / "artifact-state"
    srv = serve(str(state_dir), port=0, idle_timeout_s=None)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("INKSTITCH_CLI_ARTIFACT_DIR", str(state_dir))
    yield srv
    srv.stop()
    thread.join(timeout=5)


def _run(*args):
    result = CliRunner().invoke(root, ["--json", *args])
    assert result.exit_code == 0, result.output
    return json.loads(result.output[result.output.index("{"):])


def _post(server, path, payload):
    req = urllib.request.Request(
        f"http://127.0.0.1:{server.server_address[1]}{path}",
        data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"},
        method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


class TestArtifactCLI:
    def test_open_returns_session_url(self, server, project):
        result = _run("artifact", "open", "--project", project, "--no-browser")
        assert result["status"] == "opened"
        assert f":{server.server_address[1]}/session/" in result["url"]

    def test_poll_immediate_feedback(self, server, project):
        opened = _run("artifact", "open", "--project", project, "--no-browser")
        _post(server, f"/api/{opened['key']}/feedback",
              {"objects": ["elem_1"], "text": "move the start point up"})
        result = _run("artifact", "poll", "--project", project, "--timeout-s", "5")
        assert result["status"] == "feedback"
        assert result["items"][0]["text"] == "move the start point up"

    def test_poll_with_agent_reply_lands_in_chat(self, server, project):
        opened = _run("artifact", "open", "--project", project, "--no-browser")
        _post(server, f"/api/{opened['key']}/feedback", {"text": "hi"})
        _run("artifact", "poll", "--project", project, "--timeout-s", "5",
             "--agent-reply", "on it")
        chat = server.state.store.find_by_key(opened["key"])["chat"]
        assert ("agent", "on it") in [(m["role"], m["text"]) for m in chat]

    def test_reply(self, server, project):
        _run("artifact", "open", "--project", project, "--no-browser")
        result = _run("artifact", "reply", "--project", project, "--text", "done")
        assert result["status"] == "sent"

    def test_ask_stores_question_with_options(self, server, project):
        opened = _run("artifact", "open", "--project", project, "--no-browser")
        result = _run("artifact", "ask", "--project", project,
                      "--text", "Wider border?",
                      "--option", "yes, 50%", "--option", "keep as is")
        assert result["status"] == "sent"
        session = server.state.store.find_by_key(opened["key"])
        q = session["chat"][-1]
        assert q["role"] == "agent"
        assert q["options"] == ["yes, 50%", "keep as is"]

    def test_end_then_reopen(self, server, project):
        _run("artifact", "open", "--project", project, "--no-browser")
        assert _run("artifact", "end", "--project", project)["status"] == "ended"
        # agent-ended sessions revive on plain open
        assert _run("artifact", "open", "--project", project, "--no-browser")["status"] == "opened"

    def test_user_ended_hint(self, server, project):
        opened = _run("artifact", "open", "--project", project, "--no-browser")
        _post(server, f"/api/{opened['key']}/end", {})
        blocked = _run("artifact", "open", "--project", project, "--no-browser")
        assert blocked["status"] == "user-ended"
        assert "reopen" in blocked["hint"]

    def test_end_without_server(self, tmp_path, monkeypatch, project):
        monkeypatch.setenv("INKSTITCH_CLI_ARTIFACT_DIR", str(tmp_path / "empty"))
        assert _run("artifact", "end", "--project", project)["status"] == "no-server"

    def test_stop_without_server(self, tmp_path, monkeypatch):
        monkeypatch.setenv("INKSTITCH_CLI_ARTIFACT_DIR", str(tmp_path / "empty"))
        assert _run("artifact", "stop")["status"] == "not-running"


class TestSpawn:
    def test_open_spawns_detached_server_and_stop_kills_it(self, tmp_path, monkeypatch, project):
        state_dir = tmp_path / "spawned-state"
        monkeypatch.setenv("INKSTITCH_CLI_ARTIFACT_DIR", str(state_dir))
        result = _run("artifact", "open", "--project", project, "--no-browser")
        assert result["status"] == "opened"
        assert (state_dir / "server.json").exists()
        # session survives across CLI invocations (server holds state)
        again = _run("artifact", "open", "--project", project, "--no-browser")
        assert again["key"] == result["key"]
        assert _run("artifact", "stop")["status"] == "shutting-down"
