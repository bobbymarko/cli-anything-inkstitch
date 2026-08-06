"""Artifact session store — file-path identity + queued feedback.

Mirrors the Lavish loop's session model: sessions are keyed by the canonical
project-file path (no opaque ids — the agent reopens by path), feedback queues
locally and is never lost across reloads, and a user-initiated end blocks a
silent reopen while an agent-initiated end does not.

The store is thread-safe and persists to a JSON state file after every
mutation, so a respawned server adopts live sessions from disk.
"""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def canonical_file(path: str) -> str:
    """Resolve to an absolute canonical path (symlinks resolved, no trailing junk)."""
    return str(Path(path).expanduser().resolve())


def session_key(file: str) -> str:
    """Stable short key derived from the canonical file path."""
    return hashlib.sha256(canonical_file(file).encode("utf-8")).hexdigest()[:12]


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class SessionStore:
    """Sessions + queued feedback, persisted to `state_file`.

    Feedback lifecycle: the editor queues batches via `queue_feedback`; the
    agent's long-poll drains them via `take_feedback`. A drain is atomic —
    either the poll response carries the batches or they stay queued.
    """

    def __init__(self, state_file: str):
        self._path = Path(state_file)
        self._lock = threading.Lock()
        self._sessions: dict[str, dict[str, Any]] = {}
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text())
                self._sessions = data.get("sessions", {})
            except (json.JSONDecodeError, OSError):
                self._sessions = {}

    # -- persistence -------------------------------------------------------

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps({"sessions": self._sessions}, indent=2))
        tmp.replace(self._path)

    # -- session lifecycle -------------------------------------------------

    def keys(self) -> list[str]:
        with self._lock:
            return list(self._sessions)

    def upsert_session(self, file: str, url: str, *, reopen: bool = False) -> dict[str, Any]:
        """Open or revive the session for `file`.

        A user-ended session is only revived when `reopen` is explicitly set;
        the returned record then reports its previous status via `revived_from`.
        A session the user ended and `reopen` was not given returns the existing
        record unchanged with status "user-ended" (caller decides what to do).
        """
        file = canonical_file(file)
        key = session_key(file)
        with self._lock:
            existing = self._sessions.get(key)
            if existing and existing["status"] == "ended":
                if existing.get("ended_by") == "user" and not reopen:
                    return {**existing, "status": "user-ended"}
                existing["status"] = "open"
                existing["revived_from"] = existing.pop("ended_by", None)
                existing["url"] = url
                existing["updated_at"] = _now_iso()
                self._save()
                return dict(existing)
            if existing:
                existing["url"] = url
                existing["updated_at"] = _now_iso()
                self._save()
                return dict(existing)
            session = {
                "key": key,
                "file": file,
                "url": url,
                "status": "open",
                "ended_by": None,
                "pending_feedback": [],
                "chat": [],
                "created_at": _now_iso(),
                "updated_at": _now_iso(),
            }
            self._sessions[key] = session
            self._save()
            return dict(session)

    def find_by_key(self, key: str) -> dict[str, Any] | None:
        with self._lock:
            session = self._sessions.get(key)
            return dict(session) if session else None

    def find_by_file(self, file: str) -> dict[str, Any] | None:
        return self.find_by_key(session_key(file))

    def list_sessions(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(s) for s in self._sessions.values()]

    def end_session(self, key: str, by: str) -> dict[str, Any] | None:
        """Mark ended by "user" or "agent". Pending feedback is kept — an
        ending poll still drains it so nothing the human queued is lost."""
        with self._lock:
            session = self._sessions.get(key)
            if not session:
                return None
            session["status"] = "ended"
            session["ended_by"] = by
            session["updated_at"] = _now_iso()
            self._save()
            return dict(session)

    # -- feedback queue ------------------------------------------------------

    def queue_feedback(self, key: str, batch: dict[str, Any]) -> dict[str, Any] | None:
        """Append one feedback batch (objects + optional manipulation + text).

        The batch is also mirrored into the chat log as a human message so a
        reloaded editor can re-sync the conversation.
        """
        with self._lock:
            session = self._sessions.get(key)
            if not session:
                return None
            item = {
                "objects": batch.get("objects") or [],
                "manipulation": batch.get("manipulation"),
                "text": str(batch.get("text") or ""),
                "queued_at": _now_iso(),
            }
            session["pending_feedback"].append(item)
            session["chat"].append({"role": "human", "text": item["text"],
                                    "objects": item["objects"], "at": item["queued_at"]})
            session["updated_at"] = _now_iso()
            self._save()
            return dict(session)

    def take_feedback(self, key: str) -> dict[str, Any]:
        """Atomically drain queued feedback — AT-LEAST-ONCE delivery.

        Returns one of:
          {"status": "feedback", "items": [...]}    — batches, now removed from the queue
          {"status": "ended", "ended_by": ...}      — session over, nothing queued
          {"status": "waiting"}                     — open session, empty queue
          {"status": "unknown"}                     — no such session
        An ended session with queued feedback still returns it (flagged with
        "ended": True) so a final send-and-end never loses the payload.

        Delivered items are held unacknowledged until an agent REPLY arrives
        (reply == ack). A poll that consumes feedback and dies without
        replying leaves the items to be REDELIVERED (flagged
        "redelivered": True) to the next poll — feedback can't be lost to a
        crashed or mislaunched poll.
        """
        with self._lock:
            session = self._sessions.get(key)
            if not session:
                return {"status": "unknown"}
            unacked = session.get("unacked_feedback") or []
            for item in unacked:
                item["redelivered"] = True
            if session["pending_feedback"] or unacked:
                items = unacked + session["pending_feedback"]
                session["pending_feedback"] = []
                session["unacked_feedback"] = items
                session["updated_at"] = _now_iso()
                self._save()
                result: dict[str, Any] = {"status": "feedback", "items": items}
                if session["status"] == "ended":
                    result["ended"] = True
                    result["ended_by"] = session.get("ended_by")
                return result
            if session["status"] == "ended":
                return {"status": "ended", "ended_by": session.get("ended_by")}
            return {"status": "waiting"}

    def add_agent_reply(self, key: str, text: str,
                        options: list[str] | None = None) -> dict[str, Any] | None:
        """Agent chat message; `options` marks it a QUESTION with clickable
        answers. Questions must reach the surface the user is looking at —
        a decision prompt that renders only in the agent's own app while
        the artifact sits at 'working' reads as a hang from the editor."""
        with self._lock:
            session = self._sessions.get(key)
            if not session:
                return None
            msg: dict[str, Any] = {"role": "agent", "text": text,
                                   "at": _now_iso()}
            if options:
                msg["options"] = list(options)
            session["chat"].append(msg)
            session["unacked_feedback"] = []      # reply acknowledges delivery
            session["updated_at"] = _now_iso()
            self._save()
            return dict(session)
