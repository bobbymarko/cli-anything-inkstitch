"""Capability probe: measured per-binary fill-method support (schema/probe.py).

No version→feature table exists to mine, so support is MEASURED: a method
whose plan is byte-identical to auto fill on identical paint never reached
the stitches. Mechanics tested with a faked engine; one binary-backed test
asserts a known-good method probes supported (fixture self-check).
"""

from __future__ import annotations

import json

import pytest

from cli_anything_inkstitch.binary import discover
from cli_anything_inkstitch.schema import probe as P


@pytest.fixture
def fake_engine(monkeypatch, tmp_path):
    """Engine double: plan bytes depend on (method, paint) via a canned map,
    defaulting to the auto_fill plan — i.e. 'ignored'."""
    import cli_anything_inkstitch.binary as B

    plans = {"contour_fill": b"<svg>contour-plan</svg>"}

    def fake_run_extension(binary, ext, svg_path, args=None, ids=None,
                           capture_stdout=False, **kw):
        text = open(svg_path).read()
        method = text.split('fill_method="')[1].split('"')[0]
        return plans.get(method, b"<svg>auto-plan</svg>")

    monkeypatch.setattr(B, "run_extension", fake_run_extension)
    monkeypatch.setattr(B, "discover", lambda *a, **k: "/fake/inkstitch")
    monkeypatch.setattr(B, "detect_binary_version", lambda b: "9.9.9-test")
    from cli_anything_inkstitch.artifact import design_model as DM
    monkeypatch.setattr(DM, "extract_stitch_blocks",
                        lambda out: {"plan": out.decode()})
    from cli_anything_inkstitch.schema import cache as C
    monkeypatch.setattr(C, "cache_dir", lambda: tmp_path)
    return plans


class TestProbeMechanics:
    def test_identical_plan_means_no_effect(self, fake_engine):
        result = P.compute_and_cache(methods=["contour_fill", "cross_stitch"])
        assert result["supported"] == ["contour_fill"]
        assert result["no_effect"] == ["cross_stitch"]
        assert result["binary_version"] == "9.9.9-test"

    def test_verdicts_cached_per_binary_version(self, fake_engine, tmp_path):
        P.compute_and_cache(methods=["cross_stitch"])
        cached = P.get_cached()
        assert cached["no_effect"] == ["cross_stitch"]
        files = list(tmp_path.glob("probe-fill-methods-*.json"))
        assert len(files) == 1
        assert "9.9.9-test" in files[0].name
        assert json.loads(files[0].read_text())["no_effect"] == ["cross_stitch"]

    def test_no_binary_probes_nothing(self, monkeypatch):
        import cli_anything_inkstitch.binary as B
        monkeypatch.setattr(B, "discover", lambda *a, **k: None)
        result = P.compute_and_cache(methods=["cross_stitch"])
        assert result["no_effect"] == []
        assert "skipped" in result


@pytest.mark.skipif(discover() is None, reason="Ink/Stitch binary not installed")
class TestProbeAgainstRealBinary:
    def test_contour_probes_supported(self, monkeypatch, tmp_path):
        # contour fill exists in every engine version we support — if it
        # probes no-effect, the probe FIXTURE is broken, not the engine
        from cli_anything_inkstitch.schema import cache as C
        monkeypatch.setattr(C, "cache_dir", lambda: tmp_path)
        result = P.compute_and_cache(methods=["contour_fill"])
        assert result["supported"] == ["contour_fill"]
        assert result["no_effect"] == []
