"""Direct unit tests for history.py patch apply/reverse and ring-buffer logic."""

from __future__ import annotations

import pytest
from lxml import etree

from cli_anything_inkstitch.errors import ProjectError
from cli_anything_inkstitch.history import (
    HISTORY_LIMIT,
    apply_patch,
    attr_diff,
    can_redo,
    can_undo,
    make_entry,
    metadata_diff,
    node_delete,
    node_insert,
    push,
    subtree_replace,
)

SVG = """<svg xmlns="http://www.w3.org/2000/svg">
  <g id="layer">
    <path id="a" d="M 0 0 L 10 10" stroke="red"/>
    <path id="b" d="M 5 5 L 20 20"/>
  </g>
</svg>"""


@pytest.fixture
def tree():
    return etree.ElementTree(etree.fromstring(SVG.encode()))


def find(tree, elem_id):
    matches = tree.getroot().xpath(f"//*[@id='{elem_id}']")
    return matches[0] if matches else None


# ---- attr_diff ----

def test_attr_diff_apply_and_reverse(tree):
    patch = attr_diff("//*[@id='a']", before={"stroke": "red"}, after={"stroke": "blue"})
    apply_patch(tree, patch)
    assert find(tree, "a").get("stroke") == "blue"
    apply_patch(tree, patch, reverse=True)
    assert find(tree, "a").get("stroke") == "red"


def test_attr_diff_none_deletes_attribute(tree):
    # after=None means the command removed the attribute
    patch = attr_diff("//*[@id='a']", before={"stroke": "red"}, after={"stroke": None})
    apply_patch(tree, patch)
    assert find(tree, "a").get("stroke") is None
    apply_patch(tree, patch, reverse=True)
    assert find(tree, "a").get("stroke") == "red"


def test_attr_diff_missing_target_raises(tree):
    patch = attr_diff("//*[@id='nope']", before={}, after={"x": "1"})
    with pytest.raises(ProjectError, match="target missing"):
        apply_patch(tree, patch)


# ---- subtree_replace ----

def test_subtree_replace_apply_and_reverse(tree):
    before_xml = etree.tostring(find(tree, "a")).decode()
    after_xml = '<path xmlns="http://www.w3.org/2000/svg" id="a" d="M 0 0 L 99 99"/>'
    patch = subtree_replace("//*[@id='a']", before_xml, after_xml)
    apply_patch(tree, patch)
    assert "99" in find(tree, "a").get("d")
    apply_patch(tree, patch, reverse=True)
    assert find(tree, "a").get("d") == "M 0 0 L 10 10"


# ---- node_insert / node_delete ----

def test_node_insert_apply_and_reverse(tree):
    new_xml = '<path xmlns="http://www.w3.org/2000/svg" id="c" d="M 1 1"/>'
    patch = node_insert("//*[@id='layer']", index=1, after_xml=new_xml)
    apply_patch(tree, patch)
    assert find(tree, "c") is not None
    apply_patch(tree, patch, reverse=True)
    assert find(tree, "c") is None
    # siblings untouched
    assert find(tree, "a") is not None and find(tree, "b") is not None


def test_node_delete_apply_and_reverse(tree):
    before_xml = etree.tostring(find(tree, "b")).decode()
    patch = node_delete("//*[@id='layer']", index=1, before_xml=before_xml)
    apply_patch(tree, patch)
    assert find(tree, "b") is None
    apply_patch(tree, patch, reverse=True)
    assert find(tree, "b") is not None


def test_node_delete_refuses_mismatched_node(tree):
    """If the tree changed since recording, an index-based delete must not fire."""
    before_xml = etree.tostring(find(tree, "b")).decode()
    patch = node_delete("//*[@id='layer']", index=1, before_xml=before_xml)
    # simulate an external edit: node at index 1 is now a different element
    layer = find(tree, "layer")
    del layer[1]
    layer.insert(1, etree.fromstring(
        '<rect xmlns="http://www.w3.org/2000/svg" id="intruder" width="5" height="5"/>'
    ))
    with pytest.raises(ProjectError, match="does not match"):
        apply_patch(tree, patch)
    assert find(tree, "intruder") is not None  # nothing deleted


def test_node_insert_undo_refuses_mismatched_node(tree):
    new_xml = '<path xmlns="http://www.w3.org/2000/svg" id="c" d="M 1 1"/>'
    patch = node_insert("//*[@id='layer']", index=1, after_xml=new_xml)
    apply_patch(tree, patch)
    # external edit swaps the inserted node for something else
    layer = find(tree, "layer")
    del layer[1]
    layer.insert(1, etree.fromstring(
        '<rect xmlns="http://www.w3.org/2000/svg" id="intruder" width="5" height="5"/>'
    ))
    with pytest.raises(ProjectError, match="does not match"):
        apply_patch(tree, patch, reverse=True)


def test_unknown_patch_type_raises(tree):
    with pytest.raises(ProjectError, match="unknown patch type"):
        apply_patch(tree, {"type": "bogus"})


# ---- ring buffer ----

def _entry(i):
    return make_entry(command=f"cmd {i}", patch=metadata_diff({}, {"n": i}), scope="project")


def test_push_truncates_redo_branch():
    h = {"cursor": -1, "entries": []}
    for i in range(3):
        push(h, _entry(i))
    h["cursor"] = 0  # simulate two undos
    push(h, _entry(99))
    assert len(h["entries"]) == 2
    assert h["entries"][-1]["command"] == "cmd 99"
    assert h["cursor"] == 1
    assert not can_redo(h)


def test_push_enforces_ring_buffer_limit():
    h = {"cursor": -1, "entries": []}
    for i in range(HISTORY_LIMIT + 10):
        push(h, _entry(i))
    assert len(h["entries"]) == HISTORY_LIMIT
    assert h["cursor"] == HISTORY_LIMIT - 1
    assert h["entries"][0]["command"] == "cmd 10"  # oldest dropped
    assert can_undo(h)


# ---- oversize patch guard ----

def test_oversize_patch_is_not_stored_verbatim():
    from cli_anything_inkstitch.history import MAX_PATCH_XML_BYTES

    huge = "x" * (MAX_PATCH_XML_BYTES + 1)
    entry = make_entry("big command", subtree_replace("//*[@id='a']", huge, huge))
    assert entry["patch"]["type"] == "oversize"
    assert entry["patch"]["original_type"] == "subtree_replace"
    # the huge XML must not be in the stored entry
    import json as _json
    assert len(_json.dumps(entry)) < 1000


def test_oversize_patch_refuses_apply(tree):
    entry = make_entry("big", {"type": "oversize", "original_type": "subtree_replace",
                               "xml_bytes": 999999})
    with pytest.raises(ProjectError, match="too large"):
        apply_patch(tree, entry["patch"])


def test_normal_patch_passes_size_guard():
    patch = subtree_replace("//*[@id='a']", "<a/>", "<b/>")
    entry = make_entry("small", patch)
    assert entry["patch"]["type"] == "subtree_replace"


# ---- session metadata helpers (None == key absent) ----

def test_metadata_undo_removes_added_key():
    from cli_anything_inkstitch.commands.session import _apply_metadata, _reverse_metadata

    class FakeProj:
        session = {"units": "mm"}

    proj = FakeProj()
    # command added "thread_palette"; before records it as absent (None)
    patch = metadata_diff(before={"thread_palette": None}, after={"thread_palette": "madeira"})
    _apply_metadata(proj, patch)
    assert proj.session["thread_palette"] == "madeira"
    _reverse_metadata(proj, patch)
    assert "thread_palette" not in proj.session
    assert proj.session["units"] == "mm"
