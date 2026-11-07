import json
import sys

import pytest
from media_helpers import MEDIA_KEY, Response, catalogue_row, http_error

WAV = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"0" * 32
MP3 = b"ID3\x04" + b"0" * 32


@pytest.fixture
def stt(media, gateway, monkeypatch):
    monkeypatch.setattr(sys.modules["belive_aihub_media.transcription"], "_sleep", lambda seconds: None)
    gateway.on("GET", "/models?output_modalities=all", Response(200, {"data": [
        catalogue_row("lab/ear", "audio_transcriptions"), catalogue_row("lab/chat", "chat_completions")]}))
    return media.BeLiveTranscriptionProvider()


def recording(tmp_path, data=WAV, name="clip.wav"):
    path = tmp_path / name
    path.write_bytes(data)
    return str(path)


def test_recording_is_uploaded_as_multipart_and_the_text_returned(stt, gateway, tmp_path):
    gateway.on("POST", "/audio/transcriptions", Response(200, {"text": "hello world"}))
    result = stt.transcribe(recording(tmp_path), model="lab/ear", language="en-US")
    assert result == {"success": True, "transcript": "hello world", "provider": "belive-aihub"}
    call = gateway.calls("POST", "/audio/transcriptions")[0]
    assert call["headers"]["content-type"].startswith("multipart/form-data; boundary=")
    assert call["headers"]["authorization"] == f"Bearer {MEDIA_KEY}"
    body = call["data"]
    assert b'name="model"\r\n\r\nlab/ear' in body and b'name="language"\r\n\r\nen\r\n' in body
    assert b'name="file"; filename="recording.wav"\r\nContent-Type: audio/wav' in body and WAV in body
    assert b"prompt" not in body


def test_mp3_default_model_and_catalogue(stt, gateway, tmp_path):
    gateway.on("POST", "/audio/transcriptions", Response(200, {"text": "ok"}))
    assert stt.transcribe(recording(tmp_path, MP3, "clip.mp3"))["success"]
    body = gateway.calls("POST", "/audio/transcriptions")[0]["data"]
    assert b"Content-Type: audio/mpeg" in body and b'name="model"\r\n\r\nlab/ear' in body
    assert [m["id"] for m in stt.list_models()] == ["lab/ear"] and stt.default_model() == "lab/ear"


def test_unsupported_input_and_unlisted_model_fail_without_a_request(stt, gateway, tmp_path):
    ogg = stt.transcribe(recording(tmp_path, b"OggS" + b"0" * 40, "voice.ogg"), model="lab/ear")
    assert not ogg["success"] and ogg["transcript"] == "" and "MP3 or WAV" in ogg["error"]
    assert "lists no transcription model" in stt.transcribe(recording(tmp_path), model="lab/chat")["error"]
    assert "could not be read" in stt.transcribe(str(tmp_path / "missing.wav"))["error"]
    assert not gateway.calls("POST", "")


def test_oversized_recording_is_refused_before_upload(stt, gateway, tmp_path, monkeypatch):
    monkeypatch.setattr(sys.modules["belive_aihub_media.transcription"], "MAX_BYTES", 10)
    assert "25 MB" in stt.transcribe(recording(tmp_path), model="lab/ear")["error"]
    assert not gateway.calls("POST", "")


def test_long_recording_job_is_polled_to_its_transcript(stt, gateway, tmp_path):
    gateway.on("POST", "/audio/transcriptions", Response(202, {"id": "job_9", "status": "pending"}))
    gateway.on("GET", "/audio/transcriptions/job_9", Response(200, {"id": "job_9", "status": "in_progress"}),
               Response(200, {"id": "job_9", "status": "completed", "transcript": {"text": "long text"}}))
    assert stt.transcribe(recording(tmp_path), model="lab/ear")["transcript"] == "long text"
    assert len(gateway.calls("POST", "/audio/transcriptions")) == 1


def test_failures_return_the_error_envelope_and_never_raise(stt, gateway, tmp_path):
    gateway.on("POST", "/audio/transcriptions", http_error(402, "insufficient credit"))
    result = stt.transcribe(recording(tmp_path), model="lab/ear")
    assert result["success"] is False and result["transcript"] == ""
    assert result["error"] == "BeLive AI Hub transcription returned HTTP 402: insufficient credit"
    assert len(gateway.calls("POST", "/audio/transcriptions")) == 1
    gateway.routes = [r for r in gateway.routes if r[1] != "/audio/transcriptions"]
    gateway.on("POST", "/audio/transcriptions", Response(200, {"id": "job_9", "status": "failed",
                                                              "error": {"message": "bad audio"}}))
    assert stt.transcribe(recording(tmp_path), model="lab/ear")["error"].endswith("did not complete: bad audio")


def test_the_upload_request_id_is_returned_and_named_in_failures(stt, gateway, tmp_path):
    gateway.on("POST", "/audio/transcriptions", Response(200, {"text": "ok"}, {"x-generation-id": "req_stt_1"}))
    assert stt.transcribe(recording(tmp_path), model="lab/ear")["generation_id"] == "req_stt_1"
    gateway.routes = [r for r in gateway.routes if r[1] != "/audio/transcriptions"]
    gateway.on("POST", "/audio/transcriptions", Response(200, {"id": "job_9", "status": "failed",
                                                              "error": {"message": "bad audio"}}, {"x-generation-id": "req_stt_2"}))
    result = stt.transcribe(recording(tmp_path), model="lab/ear")
    assert result["error"] == "BeLive AI Hub transcription did not complete: bad audio (AI Hub request req_stt_2)"
    assert result["generation_id"] == "req_stt_2"
