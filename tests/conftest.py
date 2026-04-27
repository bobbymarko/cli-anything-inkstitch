import os
import shutil
from pathlib import Path

import pytest


FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def workdir(tmp_path):
    """A temp directory where tests can put project + svg files."""
    return tmp_path


@pytest.fixture
def fixture_svg(workdir):
    """Copy multi_element.svg into the workdir and return the absolute path."""
    src = FIXTURES / "multi_element.svg"
    dst = workdir / "design.svg"
    shutil.copyfile(src, dst)
    return str(dst)


@pytest.fixture
def project_path(workdir):
    return str(workdir / "project.inkstitch-cli.json")


@pytest.fixture(autouse=True)
def _isolate_caches(tmp_path, monkeypatch):
    """Use per-test cache + config dirs so tests don't poison the user's home."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
