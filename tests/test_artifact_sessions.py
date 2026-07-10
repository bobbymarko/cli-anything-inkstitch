"""Tests for the artifact session store (file-path identity + feedback queue)."""

from __future__ import annotations

import json
import threading

import pytest

from cli_anything_inkstitch.artifact.sessions import (
    SessionStore,
    canonical_file,
    session_key,
)


@pytest.fixture
def store(tmp_path):
    return SessionStore(str(tmp_path / "state.json"))


@pytest.fixture
def project(tmp_path):
    p = tmp_path / "design.inkstitch-cli.json"
    p.write_text("{}")
    return str(p)


class TestIdentity:
    def test_key_is_stable_for_same_path(self, project):
        assert session_key(project) == session_key(project)

    def test_key_survives_non_canonical_spelling(self, tmp_path, project):
        alias = str(tmp_path / "." / "design.inkstitch-cli.json")
        assert session_key(alias) == session_key(project)

    def test_canonical_file_resolves(self, tmp_path, project):
        alias = str(tmp_path / "." / "design.inkstitch-cli.json")
        assert canonical_file(alias) == canonical_file(project)


class TestLifecycle:
    def test_open_creates_session(self, store, project):
        s = store.upsert_session(project, "http://x/1")
        assert s["status"] == "open"
        assert s["key"] == session_key(project)
        assert s["file"] == canonical_file(project)

    def test_reopen_same_file_reuses_session(self, store, project):
        a = store.upsert_session(project, "http://x/1")
        b = store.upsert_session(project, "http://x/2")
        assert a["key"] == b["key"]
        assert b["url"] == "http://x/2"

    def test_agent_ended_revives_on_plain_open(self, store, project):
        s = store.upsert_session(project, "http://x/1")
        store.end_session(s["key"], "agent")
        revived = store.upsert_session(project, "http://x/1")
        assert revived["status"] == "open"
        assert revived["revived_from"] == "agent"

    def test_user_ended_blocks_plain_open(self, store, project):
        s = store.upsert_session(project, "http://x/1")
        store.end_session(s["key"], "user")
        blocked = store.upsert_session(project, "http://x/1")
        assert blocked["status"] == "user-ended"
        # store unchanged: still ended
        assert store.find_by_key(s["key"])["status"] == "ended"

    def test_user_ended_revives_with_reopen(self, store, project):
        s = store.upsert_session(project, "http://x/1")
        store.end_session(s["key"], "user")
        revived = store.upsert_session(project, "http://x/1", reopen=True)
        assert revived["status"] == "open"
        assert revived["revived_from"] == "user"


class TestFeedbackQueue:
    def test_take_on_empty_open_session_waits(self, store, project):
        s = store.upsert_session(project, "u")
        assert store.take_feedback(s["key"]) == {"status": "waiting"}

    def test_take_unknown_session(self, store):
        assert store.take_feedback("nope") == {"status": "unknown"}

    def test_queue_then_take_drains(self, store, project):
        s = store.upsert_session(project, "u")
        store.queue_feedback(s["key"], {"objects": ["elem_11"], "text": "narrower at the top"})
        result = store.take_feedback(s["key"])
        assert result["status"] == "feedback"
        assert result["items"][0]["objects"] == ["elem_11"]
        assert result["items"][0]["text"] == "narrower at the top"
        # at-least-once: drained but unacknowledged until an agent reply;
        # only after the reply does the queue read as empty
        store.add_agent_reply(s["key"], "done")
        assert store.take_feedback(s["key"]) == {"status": "waiting"}

    def test_multiple_batches_drain_together(self, store, project):
        s = store.upsert_session(project, "u")
        store.queue_feedback(s["key"], {"text": "one"})
        store.queue_feedback(s["key"], {"text": "two"})
        result = store.take_feedback(s["key"])
        assert [i["text"] for i in result["items"]] == ["one", "two"]

    def test_manipulation_delta_carried(self, store, project):
        s = store.upsert_session(project, "u")
        delta = {"op": "move-node", "rail": "A", "node": 3, "to": [10.5, 4.2]}
        store.queue_feedback(s["key"], {"objects": ["elem_2"], "manipulation": delta, "text": ""})
        result = store.take_feedback(s["key"])
        assert result["items"][0]["manipulation"] == delta

    def test_ended_session_with_queued_feedback_still_drains(self, store, project):
        s = store.upsert_session(project, "u")
        store.queue_feedback(s["key"], {"text": "final note"})
        store.end_session(s["key"], "user")
        result = store.take_feedback(s["key"])
        assert result["status"] == "feedback"
        assert result["ended"] is True
        assert result["ended_by"] == "user"
        # at-least-once: the final note redelivers until the agent replies —
        # a send-and-end payload must never be lost to a dead poll
        again = store.take_feedback(s["key"])
        assert again["status"] == "feedback"
        assert again["items"][0]["redelivered"] is True
        store.add_agent_reply(s["key"], "got it")
        assert store.take_feedback(s["key"]) == {"status": "ended", "ended_by": "user"}

    def test_feedback_mirrored_to_chat(self, store, project):
        s = store.upsert_session(project, "u")
        store.queue_feedback(s["key"], {"objects": ["elem_1"], "text": "hi"})
        store.add_agent_reply(s["key"], "done")
        chat = store.find_by_key(s["key"])["chat"]
        assert [(m["role"], m["text"]) for m in chat] == [("human", "hi"), ("agent", "done")]


class TestPersistence:
    def test_queued_feedback_survives_restart(self, tmp_path, project):
        state = str(tmp_path / "state.json")
        store = SessionStore(state)
        s = store.upsert_session(project, "u")
        store.queue_feedback(s["key"], {"text": "persisted"})
        # fresh store from the same file (server respawn)
        adopted = SessionStore(state)
        result = adopted.take_feedback(s["key"])
        assert result["status"] == "feedback"
        assert result["items"][0]["text"] == "persisted"

    def test_corrupt_state_file_starts_empty(self, tmp_path):
        state = tmp_path / "state.json"
        state.write_text("{not json")
        store = SessionStore(str(state))
        assert store.list_sessions() == []

    def test_state_file_is_valid_json(self, tmp_path, project):
        state = tmp_path / "state.json"
        store = SessionStore(str(state))
        store.upsert_session(project, "u")
        data = json.loads(state.read_text())
        assert session_key(project) in data["sessions"]


class TestConcurrency:
    def test_concurrent_queue_and_take_lose_nothing(self, store, project):
        s = store.upsert_session(project, "u")
        n = 50
        taken: list[dict] = []
        done = threading.Event()

        def producer():
            for i in range(n):
                store.queue_feedback(s["key"], {"text": f"m{i}"})
            done.set()

        def consumer():
            # a well-behaved agent: ack (reply) after every take — without
            # the ack, at-least-once delivery redelivers on purpose
            while True:
                result = store.take_feedback(s["key"])
                if result["status"] == "feedback":
                    taken.extend(result["items"])
                    store.add_agent_reply(s["key"], "ack")
                elif done.is_set():
                    break

        t1 = threading.Thread(target=producer)
        t2 = threading.Thread(target=consumer)
        t1.start(); t2.start()
        t1.join(); t2.join()
        # drain anything left
        result = store.take_feedback(s["key"])
        if result["status"] == "feedback":
            taken.extend(result["items"])
        assert sorted(i["text"] for i in taken) == sorted(f"m{i}" for i in range(n))


class TestAtLeastOnceDelivery:
    """Reply-as-ack: feedback drained by a poll that never replies gets
    REDELIVERED to the next poll (the lost-question incident, 2026-07-10)."""

    def _store(self, tmp_path):
        from cli_anything_inkstitch.artifact.sessions import SessionStore, canonical_file
        import json as _json
        proj = tmp_path / "p.inkstitch-cli.json"
        proj.write_text(_json.dumps({"schema_version": 1}))
        store = SessionStore(str(tmp_path / "state.json"))
        s = store.upsert_session(canonical_file(str(proj)), "u")
        return store, s["key"]

    def test_unreplied_feedback_redelivers(self, tmp_path):
        store, key = self._store(tmp_path)
        store.queue_feedback(key, {"text": "important question"})
        first = store.take_feedback(key)
        assert first["status"] == "feedback"
        # poll died without a reply → next take redelivers, flagged
        second = store.take_feedback(key)
        assert second["status"] == "feedback"
        assert second["items"][0]["text"] == "important question"
        assert second["items"][0]["redelivered"] is True

    def test_reply_acknowledges(self, tmp_path):
        store, key = self._store(tmp_path)
        store.queue_feedback(key, {"text": "q"})
        store.take_feedback(key)
        store.add_agent_reply(key, "answered")
        assert store.take_feedback(key)["status"] == "waiting"

    def test_new_feedback_appends_after_unacked(self, tmp_path):
        store, key = self._store(tmp_path)
        store.queue_feedback(key, {"text": "first"})
        store.take_feedback(key)                  # delivered, never acked
        store.queue_feedback(key, {"text": "second"})
        result = store.take_feedback(key)
        texts = [i["text"] for i in result["items"]]
        assert texts == ["first", "second"]
