import json
import sys

import pytest
from media_helpers import MEDIA_KEY, Response, catalogue_row, http_error

MP4 = b"\x00\x00\x00\x18ftypmp42" + b"0" * 64
BASE = "https://tokenhub-api.belive.sg"


def video_row(model_id="lab/film", **fields):
    row = catalogue_row(
        model_id, "video_generations", architecture={"input_modalities": ["text"]},
        supported_parameters=["prompt", "duration", "resolution", "aspect_ratio", "seed"],
        supported_resolutions=["480p", "720p", "1080p"], supported_aspect_ratios=["16:9", "9:16", "1:1"],
        supported_durations=[2, 3, 4, 5, 10], supported_frame_images=[])
    row.update(fields)
    return row


@pytest.fixture
def video(media, gateway, monkeypatch):
    module = sys.modules["belive_aihub_media.video"]
    monkeypatch.setattr(module, "_sleep", lambda seconds: None)
    gateway.on("GET", "/videos/models", Response(200, {"data": [video_row()]}))
    return module.BeLiveVideoGenProvider()


def job(status="pending", **fields):
    return Response(200, {"id": "job_1", "status": status, **fields})


def test_submit_once_with_idempotency_key_poll_and_download(video, gateway):
    gateway.on("POST", "/videos", job())
    gateway.on("GET", "/videos/job_1", job("in_progress"), job("completed", generation_id="gen_1", usage={"cost": 1}))
    gateway.on("GET", "/videos/job_1/content?index=0", Response(200, MP4, {"content-type": "video/mp4"}))
    result = video.generate("A red balloon", duration=2, resolution="480p", aspect_ratio="16:9", model="lab/film")
    assert result["success"] and result["job_id"] == "job_1" and result["generation_id"] == "gen_1"
    assert result["duration"] == 2 and result["resolution"] == "480p" and result["aspect_ratio"] == "16:9"
    assert open(result["video"], "rb").read() == MP4 and result["video"].endswith(".mp4")
    submit = gateway.calls("POST", "/videos")[0]
    assert json.loads(submit["data"]) == {"model": "lab/film", "prompt": "A red balloon", "duration": 2,
                                          "resolution": "480p", "aspect_ratio": "16:9"}
    assert len(submit["headers"]["idempotency-key"]) == 36
    assert submit["headers"]["authorization"] == f"Bearer {MEDIA_KEY}"
    assert len(gateway.calls("POST", "/videos", exact=True)) == 1 and len(gateway.calls("GET", "/videos/job_1", exact=True)) == 2
    assert records(sys.modules["belive_aihub_media.video"]) == []


def records(module):
    return list(sys.modules["belive_aihub_media.video"]._store_dir().glob("*.json"))


def test_options_are_clamped_to_the_models_live_values(media):
    build = sys.modules["belive_aihub_media.video"].build_body
    row = video_row(supported_resolutions=["768p", "2K"], supported_durations=[4, 8],
                    supported_aspect_ratios=["16:9", "1:1"])
    body, ignored = build(row, "p", image_url=None, reference_image_urls=None, duration=5, aspect_ratio="9:16",
                          resolution="720p", negative_prompt="blur", audio=True, seed=7)
    assert body["duration"] == 4 and body["resolution"] == "768p" and body["aspect_ratio"] == "1:1"
    assert body["seed"] == 7 and ignored == ["negative_prompt", "audio"]
    none, _ = build(video_row(supported_aspect_ratios=[], supported_resolutions=[], supported_durations=[]), "p",
                    image_url=None, reference_image_urls=None, duration=None, aspect_ratio="16:9",
                    resolution="720p", negative_prompt=None, audio=None, seed=None)
    assert none == {"model": "lab/film", "prompt": "p"}


def test_interrupted_wait_resumes_the_saved_job_without_a_second_submit(video, gateway, monkeypatch):
    module = sys.modules["belive_aihub_media.video"]
    gateway.on("POST", "/videos", job())
    gateway.on("GET", "/videos/job_1", job("in_progress"))
    monkeypatch.setattr(module, "POLL_DEADLINE", 0.0)
    first = video.generate("A red balloon", model="lab/film")
    assert not first["success"] and "job_1" in first["error"] and "same request again" in first["error"]
    assert len(records(module)) == 1
    key = gateway.calls("POST", "/videos")[0]["headers"]["idempotency-key"]
    monkeypatch.setattr(module, "POLL_DEADLINE", 900.0)
    gateway.routes = [r for r in gateway.routes if r[1] not in ("/videos/job_1",)]
    gateway.on("GET", "/videos/job_1", job("completed"))
    gateway.on("GET", "/videos/job_1/content?index=0", Response(200, MP4))
    second = video.generate("A red balloon", model="lab/film")
    assert second["success"] and len(gateway.calls("POST", "/videos")) == 1
    assert records(module) == [] and key


def test_uncertain_submit_is_reconciled_by_key_not_resubmitted(video, gateway):
    module = sys.modules["belive_aihub_media.video"]
    gateway.on("POST", "/videos", http_error(503, "overloaded"))
    first = video.generate("A red balloon", model="lab/film")
    assert "may have started" in first["error"] and len(records(module)) == 1
    key = gateway.calls("POST", "/videos")[0]["headers"]["idempotency-key"]
    gateway.on("GET", "/videos?idempotency_key=*", Response(200, {"data": [{"id": "job_1", "status": "pending"}]}))
    gateway.on("GET", "/videos/job_1", job("completed"))
    gateway.on("GET", "/videos/job_1/content?index=0", Response(200, MP4))
    second = video.generate("A red balloon", model="lab/film")
    assert second["success"] and len(gateway.calls("POST", "/videos")) == 1
    assert gateway.calls("GET", "/videos?idempotency_key=")[0]["path"].endswith(key)


def test_lookup_miss_resubmits_with_the_same_key(video, gateway):
    gateway.on("POST", "/videos", http_error(503, "overloaded"), job("completed"))
    video.generate("A red balloon", model="lab/film")
    gateway.on("GET", "/videos?idempotency_key=*", Response(200, {"data": []}))
    gateway.on("GET", "/videos/job_1", job("completed"))
    gateway.on("GET", "/videos/job_1/content?index=0", Response(200, MP4))
    assert video.generate("A red balloon", model="lab/film")["success"]
    keys = {c["headers"]["idempotency-key"] for c in gateway.calls("POST", "/videos")}
    assert len(gateway.calls("POST", "/videos")) == 2 and len(keys) == 1


def test_refusal_and_failed_job_clear_the_record(video, gateway):
    module = sys.modules["belive_aihub_media.video"]
    gateway.on("POST", "/videos", http_error(400, "bad size"))
    refused = video.generate("A red balloon", model="lab/film")
    assert refused["error"].endswith("HTTP 400: bad size") and records(module) == []
    gateway.routes.clear()
    gateway.on("GET", "/videos/models", Response(200, {"data": [video_row()]}))
    gateway.on("POST", "/videos", job())
    gateway.on("GET", "/videos/job_1", job("failed", error={"message": "content policy"}))
    failed = video.generate("A red balloon", model="lab/film")
    assert failed["error"] == "BeLive AI Hub video job job_1 failed: content policy" and records(module) == []
    assert len(gateway.calls("POST", "/videos")) == 2


def test_transient_poll_failures_are_bounded_and_a_missing_job_is_dropped(video, gateway):
    module = sys.modules["belive_aihub_media.video"]
    gateway.on("POST", "/videos", job())
    gateway.on("GET", "/videos/job_1", http_error(503), http_error(503), job("completed"))
    gateway.on("GET", "/videos/job_1/content?index=0", Response(200, MP4))
    first = video.generate("A red balloon", model="lab/film")
    assert first["success"], first["error"]
    gateway.routes = [r for r in gateway.routes if r[1] != "/videos/job_1"]
    gateway.on("GET", "/videos/job_1", http_error(503))
    stuck = video.generate("A second balloon", model="lab/film")
    assert "HTTP 503" in stuck["error"] and "may still be charged" in stuck["error"]
    assert len(gateway.calls("GET", "/videos/job_1", exact=True)) == 3 + 5
    gateway.routes = [r for r in gateway.routes if r[1] != "/videos/job_1"]
    gateway.on("GET", "/videos/job_1", http_error(404))
    assert "not found" in video.generate("A second balloon", model="lab/film")["error"]
    assert records(module) == []


def test_storage_link_is_fetched_without_the_key_and_same_origin_redirects_are_refused(video, gateway, monkeypatch, tmp_path):
    module = sys.modules["belive_aihub_media.video"]
    gateway.on("POST", "/videos", job("completed"))
    gateway.on("GET", "/videos/job_1", job("completed"))
    gateway.on("GET", "/videos/job_1/content?index=0",
               Response(302, b"", {"Location": "https://storage.example/clip.mp4?sig=1"}))
    saved = tmp_path / "clip.mp4"
    saved.write_bytes(MP4)
    seen = []
    monkeypatch.setattr(module, "save_url_video", lambda url, **kw: seen.append((url, kw)) or saved)
    assert video.generate("A red balloon", model="lab/film")["success"]
    assert seen[0][0] == "https://storage.example/clip.mp4?sig=1" and "headers" not in seen[0][1]
    gateway.routes = [r for r in gateway.routes if "content" not in r[1]]
    gateway.on("GET", "/videos/job_1/content?index=0", Response(302, b"", {"Location": "/api/v1/elsewhere"}))
    refused = video.generate("A second balloon", model="lab/film")
    assert "redirected somewhere unusable" in refused["error"] and "without a new charge" in refused["error"]


def test_download_that_is_not_mp4_keeps_the_job(video, gateway):
    module = sys.modules["belive_aihub_media.video"]
    gateway.on("POST", "/videos", job("completed"))
    gateway.on("GET", "/videos/job_1", job("completed"))
    gateway.on("GET", "/videos/job_1/content?index=0", Response(200, b"<html>not a video</html>"))
    result = video.generate("A red balloon", model="lab/film")
    assert "not an MP4" in result["error"] and len(records(module)) == 1


def test_source_images_become_frames_or_references_or_are_refused(media, gateway, video, monkeypatch):
    module = sys.modules["belive_aihub_media.video"]
    monkeypatch.setattr("tools.url_safety.is_safe_url", lambda url: "example.com" in url)
    framed = video_row("lab/i2v", architecture={"input_modalities": ["text", "image"]},
                       supported_parameters=["prompt", "frame_images"], supported_frame_images=["first_frame"])
    referenced = video_row("lab/ref", architecture={"input_modalities": ["text", "image"]},
                           supported_parameters=["prompt", "input_references"])
    build = module.build_body
    common = dict(reference_image_urls=None, duration=None, aspect_ratio="16:9", resolution="720p",
                  negative_prompt=None, audio=None, seed=None)
    body, _ = build(framed, "p", image_url="https://example.com/a.png", **common)
    assert body["frame_images"] == [{"type": "image_url", "image_url": {"url": "https://example.com/a.png"},
                                     "frame_type": "first_frame"}]
    body, _ = build(referenced, "p", image_url="https://example.com/a.png", **common)
    assert body["input_references"][0]["image_url"]["url"] == "https://example.com/a.png"
    with pytest.raises(media._api.BeLiveError, match="does not take a source image"):
        build(video_row(), "p", image_url="https://example.com/a.png", **common)
    with pytest.raises(media._api.BeLiveError, match="public https"):
        build(framed, "p", image_url="https://10.0.0.1/a.png", **common)


def test_catalogue_defaults_and_capabilities(video, media):
    assert video.default_model() == "lab/film"
    caps = video.capabilities()
    assert caps["aspect_ratios"] == ["16:9", "9:16", "1:1"] and caps["min_duration"] == 2 and caps["max_duration"] == 10
    assert caps["supports_seed"] and not caps["supports_audio"] and caps["modalities"] == ["text"]
    assert video.list_models()[0]["max_duration"] == 10
