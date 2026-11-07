import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.error import URLError

import pytest
from media_helpers import MEDIA_KEY, Response, catalogue_row

ROOT = Path(__file__).resolve().parents[1]
STAGING = "https://api-stg-tokenhub.belive.sg/api/v1"


def api():
    return sys.modules["belive_aihub_media._api"]


def test_redirects_are_never_followed(media):
    hits = []

    class Target(BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.headers.get("Authorization"))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    class Redirect(Target):
        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{target.server_port}/stolen")
            self.end_headers()

    target, redirector = HTTPServer(("127.0.0.1", 0), Target), HTTPServer(("127.0.0.1", 0), Redirect)
    for server in (target, redirector):
        threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{redirector.server_port}/api/v1/models"
        with pytest.raises(api().BeLiveError) as refused:
            api().send("GET", url, key=MEDIA_KEY, timeout=5, label="model discovery")
        assert refused.value.status == 302 and hits == []
        status, headers, _ = api().send("GET", url, key=MEDIA_KEY, timeout=5, label="download", allow_redirect=True)
        assert status == 302 and headers["Location"].endswith("/stolen") and hits == []
    finally:
        for server in (target, redirector):
            server.shutdown()


def test_only_official_bases_are_accepted(media, gateway, monkeypatch):
    gateway.on("GET", "/images/models", Response(200, {"data": []}))
    monkeypatch.setenv(api().BASE_ENV, STAGING + "/")
    api().catalogue("/images/models", "image_generations")
    assert gateway.requests[-1]["url"] == STAGING + "/images/models"
    for foreign in ("https://example.com/api/v1", "http://tokenhub-api.belive.sg/api/v1",
                    "https://tokenhub-api.belive.sg.evil.test/api/v1", "https://user:pw@tokenhub-api.belive.sg/api/v1"):
        monkeypatch.setenv(api().BASE_ENV, foreign)
        count = len(gateway.requests)
        with pytest.raises(api().BeLiveError, match="official"):
            api().catalogue("/images/models", "image_generations")
        assert len(gateway.requests) == count


def test_key_comes_from_the_active_profile_scope_not_the_launch_environment(media, gateway):
    from agent.secret_scope import reset_secret_scope, set_secret_scope
    gateway.on("GET", "/images/models", Response(200, {"data": []}))
    token = set_secret_scope({api().KEY_ENV: "active-profile"})
    try:
        api().catalogue("/images/models", "image_generations")
    finally:
        reset_secret_scope(token)
    assert gateway.requests[-1]["headers"]["authorization"] == "Bearer active-profile"
    api().catalogue("/images/models", "image_generations")
    assert gateway.requests[-1]["headers"]["authorization"] == f"Bearer {MEDIA_KEY}"
    assert len(gateway.requests) == 2  # two keys, two cache entries


def test_catalogue_is_cached_per_key_and_failures_are_remembered_briefly(media, gateway):
    gateway.on("GET", "/images/models", Response(200, {"data": [catalogue_row("lab/paint", "image_generations")]}))
    for _ in range(3):
        assert [r["id"] for r in api().catalogue("/images/models", "image_generations")] == ["lab/paint"]
    assert len(gateway.requests) == 1
    gateway.on("GET", "/videos/models", Response(503, {"error": {"message": "down"}}))
    for _ in range(3):
        with pytest.raises(api().BeLiveError, match="HTTP 503"):
            api().catalogue("/videos/models", "video_generations")
    assert len(gateway.calls("GET", "/videos/models")) == 1


@pytest.mark.parametrize("failure, expected", [
    (URLError("private upstream detail https://secret.invalid"), "network error"),
    (TimeoutError("private upstream detail"), "timed out"),
    (OSError("private upstream detail"), "network error")])
def test_network_failures_are_sanitised_and_not_retried(media, gateway, caplog, failure, expected):
    gateway.on("POST", "/images/generations", failure)
    with pytest.raises(api().BeLiveError, match=expected) as error:
        api().send_json("POST", "/images/generations", key=MEDIA_KEY, payload={"model": "m"}, timeout=5, label="image generation")
    assert "private" not in str(error.value) and MEDIA_KEY not in str(error.value)
    assert len(gateway.requests) == 1 and MEDIA_KEY not in caplog.text


def test_error_bodies_are_trimmed_to_the_gateway_message(media, gateway):
    gateway.on("GET", "/x", Response(500, b"<html>" + b"secret " * 100))
    with pytest.raises(api().BeLiveError) as error:
        api().send_json("GET", "/x", key=MEDIA_KEY, timeout=5, label="probe")
    assert str(error.value) == "BeLive AI Hub probe returned HTTP 500"
    gateway.on("GET", "/y", Response(400, {"error": {"message": "line one\nline two " + "x" * 400}}))
    with pytest.raises(api().BeLiveError) as error:
        api().send_json("GET", "/y", key=MEDIA_KEY, timeout=5, label="probe")
    assert "\n" not in str(error.value) and len(str(error.value)) < 360


def test_versions_and_manifest_agree():
    def field(path, name):
        return re.search(rf"^{name}: *\"?([^\"\n]+)\"?$", (ROOT / path).read_text(), re.MULTILINE).group(1)
    assert field("plugin.yaml", "version") == field("media/plugin.yaml", "version") == api().__version__
    assert field("media/plugin.yaml", "kind") == "backend" and field("plugin.yaml", "kind") == "model-provider"
    assert "name: BELIVE_TOKENHUB_API_KEY" in (ROOT / "media" / "plugin.yaml").read_text()
