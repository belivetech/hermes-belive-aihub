"""Load the real plugin with the released Hermes modules on Python's import path."""

import importlib.util
import sys
from pathlib import Path
from urllib.request import OpenerDirector

import pytest
from media_helpers import MEDIA_KEY, Gateway


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


@pytest.fixture(scope="session")
def media():
    """The media plugin loaded as a package, so its relative imports resolve as the host's loader does."""
    root = Path(__file__).resolve().parents[1] / "media"
    spec = importlib.util.spec_from_file_location(
        "belive_aihub_media", root / "__init__.py", submodule_search_locations=[str(root)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def gateway(media, monkeypatch):
    api = sys.modules["belive_aihub_media._api"]
    fake = Gateway()
    # Every urllib request in the process, including from a copy the host loaded itself, is answered here.
    monkeypatch.setattr(OpenerDirector, "open", lambda self, request, data=None, timeout=None: fake.open(request, timeout))
    monkeypatch.setenv(api.KEY_ENV, MEDIA_KEY)
    monkeypatch.delenv(api.BASE_ENV, raising=False)
    api._cache.clear()
    yield fake
    api._cache.clear()
