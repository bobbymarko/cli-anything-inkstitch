"""`artifact` command group — the digitizing-artifact correction loop.

Mirrors the lavish-axi CLI surface adapted to project identity:

    artifact open --project <p>        open/resume the editor session (spawns the server)
    artifact poll --project <p>        long-poll for human feedback batches
    artifact reply --project <p> ...   reply into the editor's chat
    artifact end --project <p>         end the session (agent-initiated)
    artifact stop                      shut the background server down
    artifact serve                     run the server in the foreground (internal)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import click

from cli_anything_inkstitch.commands._helpers import get_project_path
from cli_anything_inkstitch.errors import UserError
from cli_anything_inkstitch.output import emit

SPAWN_WAIT_S = 10.0


def _state_dir() -> Path:
    override = os.environ.get("INKSTITCH_CLI_ARTIFACT_DIR")
    if override:
        return Path(override)
    return Path.home() / ".cli-anything-inkstitch" / "artifact"


def _server_url(state_dir: Path) -> str | None:
    """URL of a running artifact server, or None."""
    info_path = state_dir / "server.json"
    if not info_path.exists():
        return None
    try:
        info = json.loads(info_path.read_text())
        url = f"http://127.0.0.1:{info['port']}"
        with urllib.request.urlopen(f"{url}/health", timeout=2) as r:
            if json.loads(r.read()).get("ok"):
                return url
    except (OSError, ValueError, KeyError):
        pass
    return None


def _spawn_server(state_dir: Path) -> str:
    """Start a detached server process and wait for it to come up."""
    state_dir.mkdir(parents=True, exist_ok=True)
    log = open(state_dir / "server.log", "ab")
    cmd = [sys.executable, "-m", "cli_anything_inkstitch", "artifact", "serve",
           "--state-dir", str(state_dir)]
    kwargs: dict = {"stdout": log, "stderr": log, "stdin": subprocess.DEVNULL}
    if sys.platform == "win32":
        kwargs["creationflags"] = 0x00000008 | 0x00000200  # DETACHED | NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(cmd, **kwargs)
    deadline = time.monotonic() + SPAWN_WAIT_S
    while time.monotonic() < deadline:
        url = _server_url(state_dir)
        if url:
            return url
        time.sleep(0.1)
    raise UserError(
        f"artifact server did not start within {SPAWN_WAIT_S:.0f}s "
        f"(see {state_dir / 'server.log'})"
    )


def _ensure_server(state_dir: Path) -> str:
    return _server_url(state_dir) or _spawn_server(state_dir)


def _request(url: str, *, payload: dict | None = None, timeout: float = 30) -> dict:
    if payload is not None:
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
    else:
        req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8").strip())
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode("utf-8")).get("error", "")
        except (ValueError, OSError):
            detail = ""
        raise UserError(f"artifact server error ({e.code}): {detail}") from e
    except urllib.error.URLError as e:
        raise UserError(f"artifact server unreachable: {e.reason}") from e
    except (ValueError, OSError) as e:
        # empty/truncated body or reset connection — the server shut down
        # mid-request (e.g. `artifact stop` while a long-poll was waiting)
        raise UserError(f"artifact server connection lost mid-request: {e}") from e


@click.group("artifact")
def artifact():
    """Open and drive a digitizing-artifact correction session."""


@artifact.command("serve")
@click.option("--state-dir", "state_dir", required=True, type=click.Path())
@click.option("--port", type=int, default=0)
@click.option("--idle-timeout-s", "idle_timeout_s", type=float, default=None,
              help="Self-shutdown after this long with no connections (default 30min).")
def serve_cmd(state_dir, port, idle_timeout_s):
    """Run the artifact server in the foreground (used by the spawned process)."""
    from cli_anything_inkstitch.artifact.server import DEFAULT_IDLE_TIMEOUT_S, serve
    srv = serve(state_dir, port=port,
                idle_timeout_s=idle_timeout_s if idle_timeout_s is not None else DEFAULT_IDLE_TIMEOUT_S)
    click.echo(json.dumps({"port": srv.server_address[1], "state_dir": state_dir}))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.stop()


@artifact.command("open")
@click.option("--project", "project_path", type=click.Path(), default=None)
@click.option("--reopen", is_flag=True,
              help="Revive a session the user explicitly ended from the browser.")
@click.option("--browser/--no-browser", "open_browser", default=True,
              help="Open the editor in the default browser (default: yes).")
@click.pass_context
def open_cmd(ctx, project_path, reopen, open_browser):
    """Open (or resume) the editor session for a project."""
    project = get_project_path(ctx, project_path)
    base = _ensure_server(_state_dir())
    result = _request(f"{base}/api/sessions", payload={"project": project, "reopen": reopen})
    if result.get("status") == "user-ended":
        result["hint"] = ("the user ended this session from the browser; "
                          "pass --reopen only if they asked for further review")
    elif open_browser and result.get("url"):
        import webbrowser
        webbrowser.open(result["url"])
    emit(ctx, result)


@artifact.command("poll")
@click.option("--project", "project_path", type=click.Path(), default=None)
@click.option("--timeout-s", "timeout_s", type=float, default=300.0,
              help="How long to wait for feedback before returning 'waiting'.")
@click.option("--agent-reply", "agent_reply", default=None,
              help="Send this reply into the editor chat before polling.")
@click.pass_context
def poll_cmd(ctx, project_path, timeout_s, agent_reply):
    """Long-poll for the next human feedback batch. Re-run after each result."""
    from cli_anything_inkstitch.artifact.sessions import canonical_file, session_key
    project = get_project_path(ctx, project_path)
    base = _ensure_server(_state_dir())
    if agent_reply:
        key = session_key(canonical_file(project))
        _request(f"{base}/api/{key}/agent-reply", payload={"text": agent_reply})
    from urllib.parse import quote
    try:
        result = _request(
            f"{base}/api/poll?project={quote(project)}&timeout_s={timeout_s}",
            timeout=timeout_s + 30)
    except UserError as e:
        # a poll's stdout is read programmatically (see SKILL.md "Polling
        # discipline") — a mid-poll server shutdown must come out as
        # parseable JSON, not a traceback. Queued/unacked feedback is
        # persisted by the session store and redelivered after restart.
        result = {"status": "server-lost", "detail": str(e),
                  "hint": "restart with 'artifact open'; queued feedback "
                          "is preserved and will be redelivered"}
    emit(ctx, result)


@artifact.command("reply")
@click.option("--project", "project_path", type=click.Path(), default=None)
@click.option("--text", required=True)
@click.pass_context
def reply_cmd(ctx, project_path, text):
    """Send a chat reply to the editor without polling."""
    from cli_anything_inkstitch.artifact.sessions import canonical_file, session_key
    project = get_project_path(ctx, project_path)
    base = _ensure_server(_state_dir())
    key = session_key(canonical_file(project))
    emit(ctx, _request(f"{base}/api/{key}/agent-reply", payload={"text": text}))


@artifact.command("status")
@click.option("--project", "project_path", type=click.Path(), default=None)
@click.option("--text", required=True)
@click.pass_context
def status_cmd(ctx, project_path, text):
    """Send a transient progress line to the editor's working indicator.

    Narrates in-between work ("reading the engine source…", "recomputing
    the stitch plan…") without polluting the chat history. Send one before
    each substantial step while handling feedback.
    """
    from cli_anything_inkstitch.artifact.sessions import canonical_file, session_key
    project = get_project_path(ctx, project_path)
    base = _ensure_server(_state_dir())
    key = session_key(canonical_file(project))
    emit(ctx, _request(f"{base}/api/{key}/agent-status", payload={"text": text}))


@artifact.command("end")
@click.option("--project", "project_path", type=click.Path(), default=None)
@click.pass_context
def end_cmd(ctx, project_path):
    """End the session (agent-initiated; a plain `artifact open` can revive it)."""
    project = get_project_path(ctx, project_path)
    base = _server_url(_state_dir())
    if not base:
        emit(ctx, {"status": "no-server"})
        return
    emit(ctx, _request(f"{base}/api/end", payload={"project": project}))


@artifact.command("gate")
@click.option("--project", "project_path", type=click.Path(), default=None)
@click.pass_context
def gate_cmd(ctx, project_path):
    """Run the stitchability gate. Errors should be fixed before handback."""
    from cli_anything_inkstitch.artifact.gate import run_gate
    project = get_project_path(ctx, project_path)
    emit(ctx, run_gate(project))


@artifact.command("stop")
@click.pass_context
def stop_cmd(ctx):
    """Shut down the background artifact server."""
    base = _server_url(_state_dir())
    if not base:
        emit(ctx, {"status": "not-running"})
        return
    emit(ctx, _request(f"{base}/shutdown", payload={}))
