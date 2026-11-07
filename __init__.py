"""BeLive AI Hub model provider for Hermes Agent."""

from __future__ import annotations

import json
import logging
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from agent.secret_scope import get_secret
from providers import register_provider
from providers.base import ProviderProfile

KEY_ENV = "BELIVE_TOKENHUB_API_KEY"
BASE_URL = "https://tokenhub-api.belive.sg/api/v1"
STAGING_URL = "https://api-stg-tokenhub.belive.sg/api/v1"
AUX_MODEL = "qwen/qwen3.8-flash"
_MAX_CATALOG_BYTES = 4 * 1024 * 1024
logger = logging.getLogger(__name__)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # A model-list redirect must never carry the workspace key to another origin.
        return None


def _catalog_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base not in (BASE_URL, STAGING_URL):
        raise ValueError("BeLive model discovery requires an official HTTPS API base URL")
    parsed = urlsplit(base)
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Invalid BeLive API base URL")
    return base + "/models"


def eligible_model_ids(payload: object) -> list[str]:
    """Return only catalog entries that explicitly support tool-using text chat."""
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise ValueError("Invalid BeLive model catalog")
    result: list[str] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        model_id = row.get("id")
        architecture = row.get("architecture")
        parameters = row.get("supported_parameters")
        endpoints = row.get("endpoints")
        outputs = architecture.get("output_modalities") if isinstance(architecture, dict) else None
        if (not isinstance(model_id, str) or "/" not in model_id
                or any(c.isspace() for c in model_id) or model_id in seen):
            continue
        if (not isinstance(outputs, list) or "text" not in outputs
                or not isinstance(parameters, list) or "tools" not in parameters
                or not isinstance(endpoints, list)):
            continue
        ready = False
        for endpoint in endpoints:
            if not isinstance(endpoint, dict):
                continue
            route, status = endpoint.get("route"), endpoint.get("status")
            supported = endpoint.get("supported_parameters")
            if route == "chat_completions" and status == "ready" and isinstance(supported, list) and "tools" in supported:
                ready = True
                break
        if not ready:
            continue
        seen.add(model_id)
        result.append(model_id)
    return result


class BeLiveAIHubProfile(ProviderProfile):
    def _fetch_catalog(self, *, api_key=None, base_url=None, timeout=8.0):
        # The passed credential belongs to the host's active profile. Never read ambient keys.
        key = api_key if api_key is not None else get_secret(KEY_ENV)
        if not key:
            logger.warning("BeLive AI Hub model discovery requires %s", KEY_ENV)
            return None
        url = _catalog_url(base_url or self.base_url)
        request = Request(url, headers={
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
            "User-Agent": "hermes-belive-aihub/0.1.0",
        })
        try:
            with build_opener(_NoRedirect()).open(request, timeout=timeout) as response:
                body = response.read(_MAX_CATALOG_BYTES + 1)
            if len(body) > _MAX_CATALOG_BYTES:
                raise ValueError("BeLive catalog exceeds the supported size")
            payload = json.loads(body)
            eligible_model_ids(payload)
            return payload
        except (HTTPError, URLError, TimeoutError, OSError, ValueError):
            # Do not log the upstream body, URL, exception text, or credential.
            logger.warning("BeLive AI Hub model discovery failed; using the curated fallback list")
            return None

    def fetch_models(self, *, api_key=None, base_url=None, timeout=8.0):
        payload = self._fetch_catalog(api_key=api_key, base_url=base_url, timeout=timeout)
        return eligible_model_ids(payload) if payload is not None else None

    def live_context_length(self, model, *, api_key="", base_url=""):
        # Registering a named provider disables Hermes's generic custom-endpoint probe.
        # This public hook keeps the live gateway catalog authoritative without a second registry.
        payload = self._fetch_catalog(api_key=api_key or None, base_url=base_url or None)
        if payload is None or model not in eligible_model_ids(payload):
            return None
        for row in payload["data"]:
            if isinstance(row, dict) and row.get("id") == model:
                length = row.get("context_length")
                return length if type(length) is int and length > 0 else None
        return None

    def default_vision_model(self):
        return AUX_MODEL

    def build_api_kwargs_extras(self, *, reasoning_config=None, **context):
        # AI Hub reads OpenRouter's `reasoning` object on every chat route and maps it to each
        # upstream's own control. Hermes's `supports_reasoning` flag only knows OpenRouter hosts, so
        # it is not consulted: without this hook `agent.reasoning_effort` never left the host.
        return ({"reasoning": dict(reasoning_config)} if isinstance(reasoning_config, dict) else {}), {}

    def build_extra_body(self, *, session_id=None, **context):
        # `provider_routing` is AI Hub's `provider` object. It is sent as configured: AI Hub
        # refuses a field it does not serve (for example `data_collection`) instead of ignoring it.
        prefs = context.get("provider_preferences")
        return {"provider": dict(prefs)} if prefs else {}


profile = BeLiveAIHubProfile(
    name="belive-aihub",
    display_name="BeLive AI Hub",
    description="BeLive AI Hub — unified, OpenAI-compatible model gateway",
    signup_url="https://aihub.belive.sg/",
    env_vars=(KEY_ENV,),
    base_url=BASE_URL,
    api_mode="chat_completions",
    auth_type="api_key",
    default_aux_model=AUX_MODEL,
    fallback_models=(AUX_MODEL, "z-ai/glm-5.3-flash", "deepseek/deepseek-v4.1-flash-1017"),
)

register_provider(profile)
