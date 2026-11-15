"""Ruler 0,0 = the machine file's origin.

Engine rule (lib/output.py get_origin): a global "origin" command wins,
else the centre of the stitch plan's bounding box. The editor reads the
command server-side (design.origin) and computes the stitch-bounds centre
client-side. Checked against the engine's own CSV export in Chrome and here:
the ruler reading of the first stitch equals the exported coordinate for
both branches (celtic patch: (-56.59, -14.26) mm; with an origin command
inside a translated group: (-6.34, 11.55) mm).
"""

from __future__ import annotations

from lxml import etree

from cli_anything_inkstitch.artifact.design_model import _origin_command_point

SVG = ('<svg xmlns="http://www.w3.org/2000/svg" '
       'xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 100 100">'
       '<defs><symbol id="inkstitch_origin"/></defs>{body}</svg>')
USE = '<use xlink:href="#inkstitch_origin" x="{x}" y="{y}"/>'


def _root(body):
    return etree.fromstring(SVG.format(body=body).encode())


class TestOriginCommandPoint:
    def test_point_goes_through_ancestor_transforms(self):
        # StandaloneCommand.point: the use's x/y through its transforms
        root = _root('<g transform="translate(10,5)">' + USE.format(x=40, y=30) + '</g>')
        assert _origin_command_point(root) == [50.0, 35.0]

    def test_no_origin_command(self):
        assert _origin_command_point(_root("")) is None

    def test_two_origin_commands_are_ambiguous(self):
        # the engine's global_command errors on more than one
        body = USE.format(x=1, y=1) + USE.format(x=2, y=2)
        assert _origin_command_point(_root(body)) is None

    def test_other_commands_are_not_the_origin(self):
        body = '<use xlink:href="#inkstitch_stop_position" x="5" y="5"/>'
        assert _origin_command_point(_root(body)) is None
