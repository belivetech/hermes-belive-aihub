import base64
import json
import sys

from media_helpers import MEDIA_KEY, Response, catalogue_row, http_error

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"0" * 32
SIZES = [
    {"size": "1024x1024", "aspect_ratio": "1:1", "resolution": "1K"},
    {"size": "2048x2048", "aspect_ratio": "1:1", "resolution": "2K"},
    {"size": "1360x768", "aspect_ratio": "16:9", "resolution": "1K"},
    {"size": "2688x1536", "aspect_ratio": "16:9", "resolution": "2K"},
    {"size": "768x1360", "aspect_ratio": "9:16", "resolution": "1K"},
]


def image_row(model_id="lab/paint", edits=False, sizes=SIZES):
    return catalogue_row(
        model_id, "image_generations",
        architecture={"input_modalities": ["text", "image"] if edits else ["text"]},
        supported_parameters=["input_references"] if edits else [],
        tokenhub_image_options={"sizes": sizes})


def serve(gateway, *rows):
    gateway.on("GET", "/images/models", Response(200, {"data": list(rows)}))


def generated(png=PNG, **extra):
    return Response(200, {"data": [{"b64_json": base64.b64encode(png).decode(), **extra}]},
                    {"x-request-id": "req_image_1"})


def provider(media):
    return media.BeLiveImageGenProvider()


def test_generation_request_and_cached_result(media, gateway):
    serve(gateway, image_row())
    gateway.on("POST", "/images/generations", generated(revised_prompt="a red apple"))
    result = provider(media).generate("A red apple", "landscape", model="lab/paint")
    assert result["success"] and result["provider"] == "belive-aihub" and result["modality"] == "text"
    assert result["size"] == "1360x768" and result["request_id"] == "req_image_1"
    assert result["revised_prompt"] == "a red apple"
    assert open(result["image"], "rb").read() == PNG and result["image"].endswith(".png")
    call = gateway.calls("POST", "/images/generations")[0]
    assert json.loads(call["data"]) == {"model": "lab/paint", "prompt": "A red apple", "size": "1360x768"}
    assert call["headers"]["authorization"] == f"Bearer {MEDIA_KEY}"
    assert call["url"].startswith("https://tokenhub-api.belive.sg/api/v1/")
    assert len(gateway.calls("POST", "")) == 1


def test_size_follows_aspect_and_tier(media):
    pick = sys.modules["belive_aihub_media.image"].pick_size
    row = image_row()
    assert pick(row, "square") == "1024x1024" and pick(row, "square", "2K") == "2048x2048"
    assert pick(row, "portrait") == "768x1360" and pick(row, "landscape", "2K") == "2688x1536"
    only_square = image_row(sizes=SIZES[:2])
    assert pick(only_square, "landscape") == "1024x1024"
    assert pick(image_row(sizes=[]), "square") is None


def test_unlisted_model_is_refused_before_any_charge(media, gateway):
    serve(gateway, image_row("lab/paint"))
    result = provider(media).generate("A red apple", model="other/model")
    assert not result["success"] and "lab/paint" in result["error"]
    assert not gateway.calls("POST", "")


def test_edit_uploads_source_images_as_multipart(media, gateway):
    serve(gateway, image_row("lab/edit", edits=True))
    gateway.on("POST", "/images/edits", generated())
    data_url = "data:image/png;base64," + base64.b64encode(PNG).decode()
    result = provider(media).generate("Make it blue", "square", image_url=data_url, model="lab/edit")
    assert result["success"] and result["modality"] == "image"
    call = gateway.calls("POST", "/images/edits")[0]
    assert call["headers"]["content-type"].startswith("multipart/form-data; boundary=")
    body = call["data"]
    assert b'name="model"\r\n\r\nlab/edit' in body and b'name="size"\r\n\r\n1024x1024' in body
    assert b'name="image"; filename="image-0.png"' in body and PNG in body
    assert not gateway.calls("POST", "/images/generations")


def test_edit_refused_for_models_without_references_or_bad_sources(media, gateway, tmp_path):
    serve(gateway, image_row("lab/paint"), image_row("lab/edit", edits=True))
    gen = provider(media)
    data_url = "data:image/png;base64," + base64.b64encode(PNG).decode()
    assert gen.generate("x", image_url=data_url, model="lab/paint")["error_type"] == "modality_unsupported"
    text = tmp_path / "note.png"
    text.write_text("not an image")
    for source in ("http://example.com/a.png", str(text), str(tmp_path / "missing.png")):
        result = gen.generate("x", image_url=source, model="lab/edit")
        assert not result["success"] and result["error_type"] == "api_error"
    secrets = tmp_path / ".env"
    secrets.write_text("BELIVE_TOKENHUB_API_KEY=not-an-image\n")
    refused = gen.generate("x", image_url=str(secrets), model="lab/edit")
    assert "PNG, JPEG or WebP" in refused["error"] and "not-an-image" not in json.dumps(refused)
    big = tmp_path / "big.png"
    big.write_bytes(PNG + b"0" * (7 * 1024 * 1024))
    assert "at most 7 MiB" in gen.generate("x", image_url=str(big), model="lab/edit")["error"]
    assert not gateway.calls("POST", "")


def test_local_file_and_https_source_are_read_through_hermes(media, gateway, tmp_path, monkeypatch):
    serve(gateway, image_row("lab/edit", edits=True))
    gateway.on("POST", "/images/edits", generated())
    local = tmp_path / "photo.jpg"
    local.write_bytes(JPEG)
    fetched = tmp_path / "fetched.png"
    fetched.write_bytes(PNG)
    seen = []
    monkeypatch.setattr("agent.image_gen_provider.save_url_image",
                        lambda url, **kw: seen.append(url) or fetched)
    result = provider(media).generate("x", image_url=str(local), reference_image_urls=["https://cdn.example/a.png"],
                                      model="lab/edit")
    assert result["success"] and seen == ["https://cdn.example/a.png"] and not fetched.exists()
    body = gateway.calls("POST", "/images/edits")[0]["data"]
    assert b'filename="image-0.jpg"' in body and b'filename="image-1.png"' in body


def test_api_refusal_is_reported_once_without_the_key(media, gateway):
    serve(gateway, image_row())
    gateway.on("POST", "/images/generations", http_error(402, "insufficient credit"))
    result = provider(media).generate("x", model="lab/paint")
    assert result["error"] == "BeLive AI Hub image generation returned HTTP 402: insufficient credit"
    assert MEDIA_KEY not in json.dumps(result)
    assert len(gateway.calls("POST", "/images/generations")) == 1


def test_unusable_image_data_warns_about_the_charge(media, gateway):
    serve(gateway, image_row())
    for body in (generated(b"GIF89a....."), Response(200, {"data": [{"b64_json": "###"}]}), Response(200, {"data": []})):
        gateway.routes = [r for r in gateway.routes if r[1] != "/images/generations"]
        gateway.on("POST", "/images/generations", body)
        result = provider(media).generate("x", model="lab/paint")
        assert result["error_type"] == "empty_response" and "may have been charged" in result["error"]


def test_capabilities_follow_the_active_model(media, gateway):
    serve(gateway, image_row("qwen/qwen-image-2.1-turbo"), image_row("lab/edit", edits=True))
    gen = provider(media)
    assert gen.capabilities()["modalities"] == ["text"] and gen.capabilities()["max_reference_images"] == 0
    assert gen.default_model() == "qwen/qwen-image-2.1-turbo"
    assert [m["id"] for m in gen.list_models()] == ["qwen/qwen-image-2.1-turbo", "lab/edit"]
    assert gen.list_models()[1]["modalities"] == ["text", "image"]
    assert gen.get_setup_schema()["env_vars"][0]["key"] == "BELIVE_TOKENHUB_API_KEY"


def test_missing_key_fails_before_any_request(media, gateway, monkeypatch):
    monkeypatch.delenv("BELIVE_TOKENHUB_API_KEY")
    gen = provider(media)
    assert not gen.is_available()
    result = gen.generate("x", model="lab/paint")
    assert "BELIVE_TOKENHUB_API_KEY" in result["error"] and not gateway.requests
