"""Undo/redo history — patch types per SPEC.md §1.3.1."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from lxml import etree

from cli_anything_inkstitch.errors import ProjectError
from cli_anything_inkstitch.svg.attrs import qname

import uuid


def _ulid() -> str:
    # ulid-py's API varies across versions; uuid4 is good enough for a 50-entry
    # ring buffer keyed only for human reference.
    return f"h_{uuid.uuid4().hex[:26].upper()}"


HISTORY_LIMIT = 50


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---- patch construction helpers ----

def attr_diff(target_xpath: str, before: dict, after: dict) -> dict:
    return {
        "type": "attr_diff",
        "target_xpath": target_xpath,
        "before": before,
        "after": after,
    }


def subtree_replace(target_xpath: str, before_xml: str, after_xml: str) -> dict:
    return {
        "type": "subtree_replace",
        "target_xpath": target_xpath,
        "before_xml": before_xml,
        "after_xml": after_xml,
    }


def node_insert(parent_xpath: str, index: int, after_xml: str) -> dict:
    return {
        "type": "node_insert",
        "parent_xpath": parent_xpath,
        "index": index,
        "after_xml": after_xml,
    }


def node_delete(parent_xpath: str, index: int, before_xml: str) -> dict:
    return {
        "type": "node_delete",
        "parent_xpath": parent_xpath,
        "index": index,
        "before_xml": before_xml,
    }


def metadata_diff(before: dict, after: dict) -> dict:
    return {"type": "metadata_diff", "before": before, "after": after}


# ---- entry construction + ring buffer ----

def make_entry(command: str, patch: dict, scope: str = "svg") -> dict:
    return {
        "id": _ulid(),
        "ts": _now_iso(),
        "command": command,
        "scope": scope,
        "patch": patch,
    }


def push(history: dict, entry: dict) -> None:
    """Append entry, truncating any 'redo' branch and enforcing the ring buffer."""
    cursor = history.get("cursor", -1)
    entries = history.get("entries", [])
    # drop any redo branch past cursor
    if cursor < len(entries) - 1:
        entries = entries[: cursor + 1]
    entries.append(entry)
    # ring buffer
    if len(entries) > HISTORY_LIMIT:
        drop = len(entries) - HISTORY_LIMIT
        entries = entries[drop:]
    history["entries"] = entries
    history["cursor"] = len(entries) - 1


def can_undo(history: dict) -> bool:
    return history.get("cursor", -1) >= 0


def can_redo(history: dict) -> bool:
    return history.get("cursor", -1) < len(history.get("entries", [])) - 1


def peek_undo(history: dict) -> dict | None:
    if not can_undo(history):
        return None
    return history["entries"][history["cursor"]]


def peek_redo(history: dict) -> dict | None:
    if not can_redo(history):
        return None
    return history["entries"][history["cursor"] + 1]


# ---- patch apply / reverse against an lxml tree ----

def apply_patch(tree, patch: dict, *, reverse: bool = False) -> None:
    """Apply patch to tree. With reverse=True, undo it instead."""
    ptype = patch["type"]
    if ptype == "attr_diff":
        _apply_attr_diff(tree, patch, reverse=reverse)
    elif ptype == "subtree_replace":
        _apply_subtree_replace(tree, patch, reverse=reverse)
    elif ptype == "node_insert":
        _apply_node_insert(tree, patch, reverse=reverse)
    elif ptype == "node_delete":
        _apply_node_delete(tree, patch, reverse=reverse)
    elif ptype == "metadata_diff":
        # metadata is on the project, not the SVG tree — caller handles
        pass
    else:
        raise ProjectError(f"unknown patch type: {ptype}")


def _apply_attr_diff(tree, patch: dict, *, reverse: bool) -> None:
    matches = tree.getroot().xpath(patch["target_xpath"])
    if not matches:
        raise ProjectError(f"history patch target missing: {patch['target_xpath']}")
    target = matches[0]
    desired = patch["before"] if reverse else patch["after"]
    for key, value in desired.items():
        if value is None:
            if key in target.attrib:
                del target.attrib[key]
        else:
            target.set(key, value)


def _apply_subtree_replace(tree, patch: dict, *, reverse: bool) -> None:
    matches = tree.getroot().xpath(patch["target_xpath"])
    if not matches:
        raise ProjectError(f"subtree target missing: {patch['target_xpath']}")
    old = matches[0]
    parent = old.getparent()
    if parent is None:
        raise ProjectError("cannot replace root via subtree_replace")
    new_xml = patch["before_xml"] if reverse else patch["after_xml"]
    new = etree.fromstring(new_xml)
    parent.replace(old, new)


def _apply_node_insert(tree, patch: dict, *, reverse: bool) -> None:
    matches = tree.getroot().xpath(patch["parent_xpath"])
    if not matches:
        raise ProjectError(f"insert parent missing: {patch['parent_xpath']}")
    parent = matches[0]
    if reverse:
        # undo of insert == delete the inserted node at index
        if patch["index"] >= len(parent):
            raise ProjectError("insert undo: index out of range")
        del parent[patch["index"]]
    else:
        new = etree.fromstring(patch["after_xml"])
        parent.insert(patch["index"], new)


def _apply_node_delete(tree, patch: dict, *, reverse: bool) -> None:
    matches = tree.getroot().xpath(patch["parent_xpath"])
    if not matches:
        raise ProjectError(f"delete parent missing: {patch['parent_xpath']}")
    parent = matches[0]
    if reverse:
        # undo of delete == reinsert
        new = etree.fromstring(patch["before_xml"])
        parent.insert(patch["index"], new)
    else:
        if patch["index"] >= len(parent):
            raise ProjectError("delete: index out of range")
        del parent[patch["index"]]
