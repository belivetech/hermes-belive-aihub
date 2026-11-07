import copy
import io
import json
from contextlib import contextmanager
from urllib.error import HTTPError, URLError

import httpx
import pytest


def entry(model_id="lab/chat-model"):
    return {
        "id": model_id,
        "architecture": {"output_modalities": ["text"]},
        "supported_parameters": ["tools"],
        "endpoints": [{"route": "chat_completions", "status": "ready", "supported_parameters": ["tools"]}],
    }


def test_catalog_filters_using_protocol_and_capability_metadata(plugin):
    good = entry("lab/nested/model:variant")
    rows = [good, copy.deepcopy(good), entry("not-a-qualified-id"), None]
    for field, value in [("supported_parameters", []), ("architecture", None), ("endpoints", []),
                         ("architecture", {"output_modalities": None})]:
        row = entry()
        row[field] = value
        rows.append(row)
    for change in [{"status": "disabled"}, {"route": "embeddings"},
                   {"route": "images"}, {"supported_parameters": None}]:
        row = entry()
        row["endpoints"][0].update(change)
        rows.append(row)
    assert plugin.eligible_model_ids({"data": rows}) == ["lab/nested/model:variant"]
    assert plugin.eligible_model_ids({"data": []}) == []
    with pytest.raises(ValueError):
        plugin.eligible_model_ids({"data": None})


def test_discovery_uses_profile_key_and_distinguishes_empty_from_failure(plugin, monkeypatch):
    from agent.secret_scope import reset_secret_scope, set_secret_scope

    requests = []

    class Opener:
        def open(self, request, timeout):
            requests.append((request.full_url, request.get_header("Authorization"), timeout))
            return io.BytesIO(b'{"data":[]}')

    monkeypatch.setattr(plugin, "build_opener", lambda *args: Opener())
    monkeypatch.setenv(plugin.KEY_ENV, "wrong-launch-profile")
    token = set_secret_scope({plugin.KEY_ENV: "active-profile"})
    try:
        assert plugin.profile.fetch_models() == []
        assert requests == [(plugin.BASE_URL + "/models", "Bearer active-profile", 8.0)]
        plugin.profile.fetch_models(api_key="host-passed-profile", base_url=plugin.STAGING_URL)
        assert requests[-1][1] == "Bearer host-passed-profile"
        assert requests[-1][0] == plugin.STAGING_URL + "/models"
    finally:
        reset_secret_scope(token)


@pytest.mark.parametrize("failure", [
    HTTPError("https://example.invalid", 401, "private body", {}, None),
    HTTPError("https://example.invalid", 429, "private body", {}, None),
    HTTPError("https://example.invalid", 503, "private body", {}, None),
    URLError("private upstream detail"), TimeoutError("private upstream detail"),
])
def test_discovery_failure_is_bounded_and_redacted(plugin, monkeypatch, caplog, failure):
    count = 0

    class Opener:
        def open(self, request, timeout):
            nonlocal count
            count += 1
            raise failure

    monkeypatch.setattr(plugin, "build_opener", lambda *args: Opener())
    assert plugin.profile.fetch_models(api_key="test-key") is None
    assert count == 1
    assert "private" not in caplog.text
    assert "test-key" not in caplog.text


def test_discovery_refuses_wrong_origins_and_redirects(plugin, monkeypatch):
    for base in ["http://tokenhub-api.belive.sg/api/v1", "https://example.invalid/api/v1",
                 plugin.BASE_URL + "?redirect=1", "https://tokenhub-api.belive.sg.evil.invalid/api/v1"]:
        with pytest.raises(ValueError):
            plugin.profile.fetch_models(api_key="test-key", base_url=base)
    assert plugin._NoRedirect().redirect_request(None, None, 302, "", {}, "https://example.invalid") is None


def test_missing_key_does_not_borrow_other_providers(plugin, monkeypatch):
    from agent.secret_scope import reset_secret_scope, set_secret_scope

    monkeypatch.setenv("OPENROUTER_API_KEY", "other-key")
    monkeypatch.setenv("TOKENHUB_API_KEY", "tencent-key")
    token = set_secret_scope({"OPENROUTER_API_KEY": "other-key", "TOKENHUB_API_KEY": "tencent-key"})
    try:
        assert plugin.profile.fetch_models() is None
    finally:
        reset_secret_scope(token)


@contextmanager
def scoped_profile(home, key, plugin, base_url=None):
    from agent.secret_scope import reset_secret_scope, set_secret_scope
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override

    home.mkdir()
    model = plugin.AUX_MODEL
    endpoint = {"base_url": base_url} if base_url else {}
    (home / "config.yaml").write_text(json.dumps({
        "model": {"provider": "belive-aihub", "default": model, **endpoint},
        "auxiliary": {
            "compression": {"provider": "belive-aihub", "model": model, **endpoint},
            "vision": {"provider": "belive-aihub", "model": model, **endpoint},
        },
    }))
    home_token = set_hermes_home_override(home)
    secret_token = set_secret_scope({plugin.KEY_ENV: key}, profile_home=str(home))
    try:
        yield
    finally:
        reset_secret_scope(secret_token)
        reset_hermes_home_override(home_token)


@pytest.mark.parametrize("staging", [False, True])
def test_real_host_auxiliary_transport_uses_each_profiles_key(plugin, tmp_path, monkeypatch, staging):
    from agent.auxiliary_client import get_text_auxiliary_client, resolve_vision_provider_client
    from hermes_cli.auth import resolve_provider
    from providers import get_provider_profile

    calls = []

    def send(client, request, **kwargs):
        calls.append((str(request.url), request.headers.get("authorization"), json.loads(request.content)))
        return httpx.Response(200, request=request, json={
            "id": "fixture-completion", "object": "chat.completion", "created": 0,
            "model": plugin.AUX_MODEL,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "OK"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        })

    monkeypatch.setattr(httpx.Client, "send", send)
    monkeypatch.setenv(plugin.KEY_ENV, "wrong-launch-profile")
    assert get_provider_profile("belive-aihub").name == "belive-aihub"
    assert resolve_provider("tokenhub", explicit_api_key="tencent-fixture-key") == "tencent-tokenhub"
    base_url = plugin.STAGING_URL if staging else plugin.BASE_URL
    for name in ("profile-a", "profile-b"):
        key = name + "-fake-key"
        with scoped_profile(tmp_path / name, key, plugin, base_url if staging else None):
            compression, model = get_text_auxiliary_client("compression")
            assert compression is not None
            compression.chat.completions.create(model=model, messages=[{"role": "user", "content": "Compress this"}])
            compression.close()
            provider, vision, model = resolve_vision_provider_client()
            assert provider == "belive-aihub"
            assert vision is not None
            vision.chat.completions.create(model=model, messages=[{"role": "user", "content": [
                {"type": "text", "text": "Read image"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
            ]}])
            vision.close()
        assert all(auth == "Bearer " + key for _, auth, _ in calls[-2:])
    assert len(calls) == 4
    assert all(url == base_url + "/chat/completions" for url, _, _ in calls)
    assert all(body["model"] == plugin.AUX_MODEL for _, _, body in calls)


def test_host_context_resolver_uses_live_catalog_for_named_provider(plugin, tmp_path, monkeypatch):
    from agent.model_metadata import get_model_context_length

    row = entry("belive-fixture/context-model")
    row["context_length"] = 1000000
    calls = []

    class Opener:
        def open(self, request, timeout):
            calls.append(request.full_url)
            return io.BytesIO(json.dumps({"data": [row]}).encode())

    monkeypatch.setattr(plugin, "build_opener", lambda *args: Opener())
    with scoped_profile(tmp_path / "context-home", "profile-key", plugin):
        assert get_model_context_length(
            row["id"], base_url=plugin.BASE_URL, api_key="profile-key", provider="belive-aihub",
        ) == 1000000
    assert calls == [plugin.BASE_URL + "/models"]


@pytest.mark.parametrize("reasoning, prefs, body", [
    ({"enabled": True, "effort": "medium"}, {"order": ["bitdeer-ai"], "require_parameters": True},
     {"reasoning": {"enabled": True, "effort": "medium"}, "provider": {"order": ["bitdeer-ai"], "require_parameters": True}}),
    ({"enabled": False}, None, {"reasoning": {"enabled": False}}),
    (None, None, {}),
])
def test_host_transport_sends_reasoning_and_provider_routing(plugin, reasoning, prefs, body):
    # The host's own request builder, with Hermes's OpenRouter-only reasoning flag off as it is for this base URL.
    from agent.transports.chat_completions import ChatCompletionsTransport
    from providers import get_provider_profile

    kwargs = ChatCompletionsTransport().build_kwargs(
        "z-ai/glm-5.3-flash", [{"role": "user", "content": "Say OK"}], None,
        provider_profile=get_provider_profile("belive-aihub"), base_url=plugin.BASE_URL,
        reasoning_config=reasoning, provider_preferences=prefs, supports_reasoning=False)
    sent = kwargs.get("extra_body") or {}
    assert {k: sent[k] for k in ("reasoning", "provider") if k in sent} == body
    assert "reasoning_effort" not in kwargs
