"""Load the real plugin with the released Hermes modules on Python's import path."""

import importlib.util
import sys
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))


@pytest.fixture(scope="session")
def plugin():
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("belive_aihub_plugin", root / "__init__.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module
