"""Failure-path qualification for the media backends.

What each paid call does when AI Hub refuses the key (401), the balance (402) or the rate (429), or the
upstream fails (502/503), and when the network drops. Every answer names the status, the gateway's
message and the request id, never the key, and no paid request is sent twice."""
import sys
from urllib.error import URLError

import pytest
from media_helpers import MEDIA_KEY, Response, catalogue_row
from test_media_image import image_row, serve
from test_media_video import video_row

WAV = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"0" * 32
FAILURES = [(401, "invalid API key"), (402, "insufficient credit"), (429, "rate limit exceeded"),
            (502, "upstream provider failed"), (503, "model temporarily unavailable")]


def refusal(status, message):
    return Response(status, {"error": {"message": message}},
                    {"x-generation-id": f"req_fail{status}", **({"retry-after": "1"} if status == 429 else {})})


def named(status, message):
    return f"returned HTTP {status}: {message} (AI Hub request req_fail{status})"


def image_call(media, gateway, tmp_path):
    serve(gateway, image_row())
    result = media.BeLiveImageGenProvider().generate("x", model="lab/paint")
    return result.get("error"), "/images/generations"


def speech_call(media, gateway, tmp_path):
    gateway.on("GET", "/models?output_modalities=audio", Response(200, {"data": [catalogue_row("lab/voice", "audio_speech")]}))
    try:
        media.BeLiveTTSProvider().synthesize("Hi", str(tmp_path / "a.mp3"), model="lab/voice")
    except media._api.BeLiveError as exc:
        return str(exc), "/audio/speech"
    return None, "/audio/speech"


def transcription_call(media, gateway, tmp_path):
    gateway.on("GET", "/models?output_modalities=all", Response(200, {"data": [catalogue_row("lab/ear", "audio_transcriptions")]}))
    clip = tmp_path / "clip.wav"
    clip.write_bytes(WAV)
    result = media.BeLiveTranscriptionProvider().transcribe(str(clip), model="lab/ear")
    assert result["success"] is False
    return result["error"], "/audio/transcriptions"


@pytest.fixture(autouse=True)
def no_waiting(media, monkeypatch):
    monkeypatch.setattr(sys.modules["belive_aihub_media.video"], "_sleep", lambda seconds: None)


def video_call(media, gateway, tmp_path):
    module = sys.modules["belive_aihub_media.video"]
    gateway.on("GET", "/videos/models", Response(200, {"data": [video_row()]}))
    gateway.on("GET", "/videos?idempotency_key=*", Response(200, {"data": []}))
    result = module.BeLiveVideoGenProvider().generate("A red balloon", model="lab/film")
    return result.get("error"), "/videos"


CALLS = {"image": image_call, "speech": speech_call, "transcription": transcription_call, "video": video_call}


@pytest.mark.parametrize("status,message", FAILURES)
@pytest.mark.parametrize("name", list(CALLS))
def test_each_refusal_is_named_and_the_paid_request_is_sent_once(media, gateway, tmp_path, name, status, message):
    gateway.on("POST", {"image": "/images/generations", "speech": "/audio/speech",
                        "transcription": "/audio/transcriptions", "video": "/videos"}[name], refusal(status, message))
    error, path = CALLS[name](media, gateway, tmp_path)
    assert error is not None and named(status, message) in error
    assert MEDIA_KEY not in error
    assert len(gateway.calls("POST", path, exact=True)) == 1


@pytest.mark.parametrize("name", list(CALLS))
def test_a_dropped_connection_is_a_network_error_and_is_not_resent(media, gateway, tmp_path, name):
    path = {"image": "/images/generations", "speech": "/audio/speech",
            "transcription": "/audio/transcriptions", "video": "/videos"}[name]
    gateway.on("POST", path, URLError("connection reset"))
    error, _ = CALLS[name](media, gateway, tmp_path)
    assert error is not None and "failed: network error" in error
    assert len(gateway.calls("POST", path, exact=True)) == 1


def test_video_keeps_its_record_only_when_the_job_may_have_started(media, gateway, tmp_path):
    module = sys.modules["belive_aihub_media.video"]
    for status, message in FAILURES:
        gateway.routes.clear()
        gateway.on("POST", "/videos", refusal(status, message))
        error, _ = video_call(media, gateway, tmp_path)
        records = list(module._store_dir().glob("*.json"))
        if status in (401, 402):
            assert records == [], status
        else:
            assert "run the same request again and the saved Idempotency-Key reconciles it" in error
            assert len(records) == 1, status
            for record in records:
                record.unlink()


def test_video_polling_waits_at_least_the_retry_after(media, gateway, tmp_path, monkeypatch):
    module = sys.modules["belive_aihub_media.video"]
    waits = []
    gateway.on("GET", "/videos/models", Response(200, {"data": [video_row()]}))
    gateway.on("POST", "/videos", Response(200, {"id": "job_1", "status": "pending"}))
    gateway.on("GET", "/videos/job_1", Response(429, {"error": {"message": "slow down"}}, {"retry-after": "30"}),
               Response(200, {"id": "job_1", "status": "failed", "error": {"message": "stop"}}))
    monkeypatch.setattr(module, "_sleep", waits.append)
    module.BeLiveVideoGenProvider().generate("A red balloon", model="lab/film")
    assert waits and waits[0] >= 30
