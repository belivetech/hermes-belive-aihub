# BeLive AI Hub media backends for Hermes Agent

Adds [BeLive AI Hub](https://aihub.belive.sg/) as the backend for Hermes's `image_generate`, `video_generate`, `text_to_speech` and speech-to-text. It is a second plugin in this repository, separate from the `belive-aihub` model provider: Hermes loads a model provider and a media backend through different mechanisms, so each installs on its own. Requires Hermes **0.22.7 or later**. BeLive maintains this MIT-licensed plugin; support and source are in this repository's issues. Owner contact: Hassan / BeLive Technology.

## Install and configure

```sh
hermes plugins install belivetech/hermes-belive-aihub#media --enable
```

Create a workspace inference key in BeLive AI Hub and make it available as `BELIVE_TOKENHUB_API_KEY` through the active profile's private `.env`. Use the same key as the `belive-aihub` model provider; do not use a console login or management key. The plugin reads only that variable, through Hermes's profile-aware secret scope, and sends it only to the official AI Hub API. Select the backend for each tool you want, with `hermes tools` or in `config.yaml`:

```yaml
image_gen:
  provider: belive-aihub
  model: qwen/qwen-image-2.1-turbo   # optional
video_gen:
  provider: belive-aihub
  model: alibaba/wan3.0-video        # optional
tts:
  provider: belive-aihub
  model: qwen/qwen-audio-3.0-tts-flash   # optional
  voice: loongjohn                       # optional, case-sensitive, belongs to the model
  output_format: mp3                     # mp3, wav, flac, opus, ogg, aac or pcm
  belive-aihub:
    max_text_length: 600                 # AI Hub takes 600 characters per request; Hermes splits longer text
stt:
  provider: belive-aihub
  belive-aihub:
    model: qwen/qwen-audio-3.0-asr-flash # optional
```

| Hermes tool | Setting | AI Hub route | Default model |
| --- | --- | --- | --- |
| `image_generate` | `image_gen.provider` | `POST /images/generations`; with a source image `POST /images/edits` | `qwen/qwen-image-2.1-turbo` |
| `video_generate` | `video_gen.provider` | `POST /videos`, poll `GET /videos/{id}`, `GET /videos/{id}/content` | `alibaba/wan3.0-video` |
| `text_to_speech` | `tts.provider` | `POST /audio/speech` | `qwen/qwen-audio-3.0-tts-flash`, voice `loongjohn` |
| speech-to-text (`stt.provider`, voice mode, inbound audio) | `stt.provider` | `POST /audio/transcriptions` | `qwen/qwen-audio-3.0-asr-flash` |

Image and video understanding is not part of this plugin. `vision_analyze` and `video_analyze` use Hermes's auxiliary vision client, so point `auxiliary.vision` (and `auxiliary.video`) at the `belive-aihub` model provider as described in the main [README](../README.md).

## How it behaves

- **Live catalogues.** Models come from the account's own `GET /images/models`, `GET /videos/models`, `GET /models?output_modalities=audio` and `GET /models?output_modalities=all`; the model pickers read the same rows. Nothing is bundled, so a model AI Hub adds is usable without a plugin release. A model that is not listed is refused, with the models that are, before any paid request. Catalogues are cached for five minutes per key.
- **One request, no retry.** Within one call, a request that can create a charge is sent once, with a bounded timeout, and is never retried or redirected. Running a video request again after a failed submit sends one more submit under the same `Idempotency-Key` if the lookup by key finds no job (see the table below). Redirects are refused; the key goes only to the official base (`https://tokenhub-api.belive.sg/api/v1`). For qualification, set `BELIVE_TOKENHUB_BASE_URL=https://api-stg-tokenhub.belive.sg/api/v1` and use a staging key; no other base is accepted.
- **Images.** The semantic aspect (`landscape`, `square`, `portrait`) picks the model's own nearest published size, defaulting to its 1K tier; the `resolution` control (`1K`, `2K`) picks the larger tier. A source image (`image_url` or `reference_image_urls`: a local file, an https URL or a `data:` URL, PNG, JPEG or WebP up to 7 MiB) goes to `/images/edits` on models whose catalogue lists `input_references`; other models refuse it before any charge. A file is uploaded only if its leading bytes are an image. An https URL is fetched through Hermes's guarded downloader.
- **Video is resumable.** The submit carries an `Idempotency-Key`, and the key and job id are saved under the profile's `plugin-data/belive-aihub-media/video-jobs/` (no credential; removed when the video is saved or the job ends). If the wait is interrupted, or the call ends before the job does (up to 15 minutes), run the same request again: it finds the saved job and polls and downloads it, with no second job and no second charge. A failed, cancelled or expired job clears the record. Duration, resolution and aspect ratio are clamped to the model's own values (Hermes always sends a default resolution and aspect ratio); the result reports what was used, and options a model does not take are dropped and listed in `ignored_options`. Source images become a first frame or reference images where the model lists them. A storage link, if the gateway answers with one, is fetched through Hermes's guarded downloader without the key.
- **Speech** takes 1 to 600 characters per request; `speed` is ignored because AI Hub has no speed control. `ogg` is sent as `opus` (the gateway returns Ogg Opus; it has no `ogg` format). Voices belong to a model and are case-sensitive; a wrong voice is refused with the model's list.
- **Transcription** takes MP3 or WAV recordings up to 25 MB (mono). Convert other formats first, for example `ffmpeg -i voice.ogg -ac 1 voice.wav`. A long recording that AI Hub answers with a job is polled for up to five minutes.
- **Charges.** Each call is billed to the workspace on its AI Hub key with no Hermes approval prompt. Check Activity for actual usage and charges; the generation id each image result and video job reports is the id shown there.
- **Request ids for support.** Image results, video jobs and transcriptions report the AI Hub request id as `generation_id`, and every failure that reached AI Hub ends with `(AI Hub request <id>)`. Look a completed request up with `GET /generation?id=<id>` (model, provider, cost); a request AI Hub refused before running it is not charged and is not listed there, so quote its id to BeLive support.

### Diagnostics and request counts by feature

A request id is shown only where Hermes's result has room for it. "Attempts" counts HTTP requests to AI Hub in one call; "jobs" and "charges" count what AI Hub creates and bills.

| Feature | Request id on success | Request id on failure | Attempts per call | Jobs and charges |
| --- | --- | --- | --- | --- |
| Image generation and edits | `generation_id` | Yes | 1 | At most 1 charge |
| Video generation | `generation_id` and the job id | Yes | 1 submit, then polls and one download. A rerun after a 429, 5xx or dropped connection looks the saved key up and submits again under the same key only if no job is found | 1 job per key, charged once (staging, 2026-11-07: interrupted and rerun, one submit, one charge) |
| Speech | None: Hermes's speech result is a file path | Yes | 1 | At most 1 charge |
| Transcription | `generation_id` | Yes | 1 upload; a job answer is polled, not re-uploaded | At most 1 charge |
| Chat, `vision_analyze`, `video_analyze` | Not added: Hermes's own transport | Hermes's own message | Set by Hermes. Hermes v0.23.0 resends a chat request whose stream breaks, even with `agent.api_max_retries: 0` ([VALIDATION.md](../VALIDATION.md)) | Each resend is a separate charge |

For support:
- **Completed request:** look it up with `GET /generation?id=<id>` (model, provider, cost).
- **Request AI Hub refused before running it** (`GET /generation` answers 404): it was not charged. Quote the id from the error to BeLive support.
- **Speech and chat that succeeded:** find them in Activity by time and model.

## Not provided

Hermes exposes plugin surfaces only for image generation, video generation, text-to-speech and speech-to-text. These AI Hub capabilities have none, so they are not covered: music and other audio generation (`/audio/generations`), 3D generation, asynchronous long-form speech jobs, speech models served on the chat route (Xiaomi MiMo TTS), video reference inputs such as Kling Motion Control, `n` greater than 1 for images and live (streaming) transcription. Use the API directly for these.

## Update, remove and development

Use Hermes's plugin update and remove commands for `belive-aihub-media`; removal deletes no key. The plugin has no telemetry, self-updater, credential-file reader or background service. It registers four backends and no tools, hooks, commands or threads. Tests and validation are in [VALIDATION.md](../VALIDATION.md).
