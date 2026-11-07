"""``image_generate`` backend: ``POST /images/generations`` and, for source images, ``POST /images/edits``."""

from __future__ import annotations

import base64
import math
from typing import Any

from agent.image_gen_provider import (
    DEFAULT_ASPECT_RATIO,
    ImageGenProvider,
    error_response,
    normalize_reference_images,
    resolve_aspect_ratio,
    save_b64_image,
    success_response,
)

from . import _api
from ._api import BeLiveError
from ._sources import image_bytes, sniff_image

DEFAULT_MODEL = "qwen/qwen-image-2.1-turbo"
# Some models run up to 300 s inside the request; an uncertain image is never resubmitted.
REQUEST_TIMEOUT = 330.0
MAX_IMAGE_BYTES = 64 * 1024 * 1024
MAX_SOURCES = 16
_REFERENCES_PER_CALL = 4
_ASPECTS = {"landscape": 16 / 9, "square": 1.0, "portrait": 9 / 16}
_TIER_PIXELS = {"1K": 1024 * 1024, "2K": 2048 * 2048}
_ROUTE_PATH, _ROUTE = "/images/models", "image_generations"


def _ratio(text: str) -> float | None:
    try:
        width, height = (float(part) for part in text.split(":"))
        return width / height if width > 0 and height > 0 else None
    except ValueError:
        return None


def _area(size: str) -> int | None:
    try:
        width, height = (int(part) for part in size.lower().split("x"))
        return width * height
    except ValueError:
        return None


def offered_sizes(row: dict) -> list[dict[str, str]]:
    options = row.get("tokenhub_image_options")
    sizes = options.get("sizes") if isinstance(options, dict) else None
    return [s for s in sizes if isinstance(s, dict) and all(isinstance(s.get(k), str) for k in ("size", "aspect_ratio"))
            ] if isinstance(sizes, list) else []


def pick_size(row: dict, aspect: str, tier: str = "1K") -> str | None:
    """The model's own ``size`` string nearest the semantic aspect and the 1K/2K tier, or ``None``
    when the model publishes no sizes (the gateway then applies the model's default)."""
    sizes = [s for s in offered_sizes(row) if _ratio(s["aspect_ratio"]) and _area(s["size"])]
    if not sizes:
        return None
    target = _ASPECTS.get(aspect, 1.0)
    best = min({s["aspect_ratio"] for s in sizes}, key=lambda a: abs(math.log(_ratio(a) / target)))
    wanted = _TIER_PIXELS.get(tier, _TIER_PIXELS["1K"])
    return min((s for s in sizes if s["aspect_ratio"] == best), key=lambda s: abs(_area(s["size"]) - wanted))["size"]


def accepts_references(row: dict) -> bool:
    return "image" in _api.input_modalities(row) and "input_references" in _api.str_list(row.get("supported_parameters"))


def _configured_model() -> str | None:
    try:
        from hermes_cli.config import load_config
        section = load_config().get("image_gen")
        value = section.get("model") if isinstance(section, dict) else None
    except Exception:
        return None
    return value.strip() if isinstance(value, str) and value.strip() else None


class BeLiveImageGenProvider(ImageGenProvider):
    @property
    def name(self) -> str:
        return _api.PROVIDER

    @property
    def display_name(self) -> str:
        return "BeLive AI Hub"

    def is_available(self) -> bool:
        return _api.has_key()

    def get_setup_schema(self) -> dict[str, Any]:
        return {"name": "BeLive AI Hub", "badge": "paid", "tag": "Live AI Hub image catalogue, text-to-image and edits",
                "env_vars": [{"key": _api.KEY_ENV, "prompt": "BeLive AI Hub workspace inference key",
                              "url": "https://aihub.belive.sg/"}]}

    def _rows(self) -> list[dict]:
        try:
            return _api.catalogue(_ROUTE_PATH, _ROUTE)
        except BeLiveError:
            return []

    def list_models(self) -> list[dict[str, Any]]:
        return [{"id": r["id"], "display": r.get("name") or r["id"],
                 "modalities": ["text", "image"] if accepts_references(r) else ["text"]} for r in self._rows()]

    def default_model(self) -> str | None:
        rows = self._rows()
        return _api.default_id(rows, DEFAULT_MODEL) if rows else DEFAULT_MODEL

    def capabilities(self) -> dict[str, Any]:
        row = next((r for r in self._rows() if r["id"] == (_configured_model() or self.default_model())), None)
        edits = row is not None and accepts_references(row)
        return {"modalities": ["text", "image"] if edits else ["text"],
                "max_reference_images": _REFERENCES_PER_CALL if edits else 0,
                "creative_controls": ["resolution"]}

    def generate(self, prompt: str, aspect_ratio: str = DEFAULT_ASPECT_RATIO, *, image_url: str | None = None,
                 reference_image_urls: list[str] | None = None, **kwargs: Any) -> dict[str, Any]:
        prompt = (prompt or "").strip()
        aspect = resolve_aspect_ratio(aspect_ratio)
        model_id = (kwargs.get("model") or "").strip() or self.default_model() or DEFAULT_MODEL

        def fail(message: str, kind: str) -> dict[str, Any]:
            return error_response(error=message, error_type=kind, provider=self.name, model=model_id,
                                  prompt=prompt, aspect_ratio=aspect)

        if not prompt:
            return fail("Prompt is required", "invalid_input")
        sources = ([image_url.strip()] if isinstance(image_url, str) and image_url.strip() else []) \
            + (normalize_reference_images(reference_image_urls) or [])
        try:
            key = _api.require_key()
            row = _api.find_model(_api.catalogue(_ROUTE_PATH, _ROUTE), model_id, "image generation")
            if sources and not accepts_references(row):
                return fail(f"{model_id} does not accept source images; pick an edit-capable model",
                            "modality_unsupported")
            if len(sources) > MAX_SOURCES:
                return fail(f"BeLive AI Hub takes at most {MAX_SOURCES} source images", "invalid_input")
            fields = {"model": model_id, "prompt": prompt}
            size = pick_size(row, aspect, str(kwargs.get("resolution") or "1K").upper())
            if size:
                fields["size"] = size
            if sources:
                files = [("image", f"image-{i}.{_EXTENSIONS[mime]}", mime, data)
                         for i, (data, mime) in enumerate(image_bytes(s) for s in sources)]
                _, headers, payload = _api.send_json(
                    "POST", "/images/edits", key=key, form=_api.multipart(fields, files),
                    timeout=REQUEST_TIMEOUT, label="image edit")
            else:
                _, headers, payload = _api.send_json(
                    "POST", "/images/generations", key=key, payload=fields,
                    timeout=REQUEST_TIMEOUT, label="image generation")
        except BeLiveError as exc:
            return fail(str(exc), "api_error")
        first = _first_image(payload)
        raw = _decode(first.get("b64_json"))
        mime = sniff_image(raw) if raw else None
        if not mime or len(raw) > MAX_IMAGE_BYTES:
            return fail(_api.with_request_id(
                "BeLive AI Hub returned no usable PNG, JPEG or WebP data; the request may have been "
                "charged, so check Activity before retrying", _api.generation_id(headers)), "empty_response")
        path = save_b64_image(first["b64_json"], prefix=self.name, extension=_EXTENSIONS[mime])
        extra: dict[str, Any] = {"size": size, "generation_id": _api.generation_id(headers)}
        if isinstance(first.get("revised_prompt"), str):
            extra["revised_prompt"] = first["revised_prompt"]
        return success_response(image=str(path), model=model_id, prompt=prompt, aspect_ratio=aspect,
                                provider=self.name, modality="image" if sources else "text", extra=extra)


_EXTENSIONS = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}


def _first_image(payload: Any) -> dict:
    data = payload.get("data") if isinstance(payload, dict) else None
    return data[0] if isinstance(data, list) and data and isinstance(data[0], dict) else {}


def _decode(encoded: Any) -> bytes | None:
    try:
        return base64.b64decode(encoded, validate=True) if isinstance(encoded, str) else None
    except ValueError:
        return None
